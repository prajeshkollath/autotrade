"""
start_strategy.py — Launched by the strategy hub to start a strategy.

Flow:
  1. Load strategy config from data/strategies.json
  2. Check OpenAlgo positionbook — skip entry if positions already exist
  3. If no positions: quick entry (scan chain, place CE + PE SELL orders)
  4. Get current spot → hand off to position_manager via os.execv

Usage:
  python agents/start_strategy.py --id nifty_short_strangle
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

IST = timezone(timedelta(hours=5, minutes=30))
BASE_DIR   = Path(__file__).parent.parent
OPENALGO_BASE = "http://localhost:5000"
STRATEGIES_FILE = BASE_DIR / "data" / "strategies.json"


def _load_strategies() -> list[dict]:
    if not STRATEGIES_FILE.exists():
        return []
    return json.loads(STRATEGIES_FILE.read_text())


def _oa_headers() -> dict:
    return {"Content-Type": "application/json"}


MCX_UNDERLYINGS = {"GOLDM", "GOLD", "SILVER", "CRUDEOIL", "NATURALGAS"}


def _get_spot_mcx(api_key: str, underlying: str) -> float:
    import sqlite3 as _sq
    try:
        conn = _sq.connect("/home/freed/openalgo/db/openalgo.db")
        row  = conn.execute(
            "SELECT symbol FROM symtoken WHERE exchange='MCX' AND symbol LIKE ? "
            "AND instrumenttype='FUT' ORDER BY expiry LIMIT 1",
            (f"{underlying}%",)
        ).fetchone()
        conn.close()
        if not row: return 0.0
        fut_sym = row[0]
        r = requests.post(f"{OPENALGO_BASE}/api/v1/quotes",
                          json={"apikey": api_key, "symbol": fut_sym, "exchange": "MCX"},
                          headers=_oa_headers(), timeout=8)
        ltp = r.json().get("data", {}).get("ltp") or r.json().get("ltp")
        print(f"[start_strategy] MCX spot via {fut_sym}: {ltp}")
        return float(ltp) if ltp else 0.0
    except Exception as e:
        print(f"[start_strategy] MCX spot error: {e}")
        return 0.0


def _get_spot(api_key: str, underlying: str) -> float:
    if underlying in MCX_UNDERLYINGS:
        return _get_spot_mcx(api_key, underlying)
    _exch = "NSE_INDEX" if underlying in {"NIFTY", "BANKNIFTY", "SENSEX"} else "NSE"
    try:
        r = requests.post(f"{OPENALGO_BASE}/api/v1/quotes",
                          json={"apikey": api_key, "symbol": underlying,
                                "exchange": _exch},
                          headers=_oa_headers(), timeout=5)
        data = r.json()
        ltp = data.get("data", {}).get("ltp") or data.get("ltp")
        if ltp:
            return float(ltp)
    except Exception as e:
        print(f"[start_strategy] spot fetch error: {e}")
    return 0.0
def _get_positions(api_key: str) -> list[dict]:
    try:
        r = requests.post(f"{OPENALGO_BASE}/api/v1/positionbook",
                          json={"apikey": api_key},
                          headers=_oa_headers(), timeout=8)
        data = r.json()
        return data.get("data", data if isinstance(data, list) else [])
    except Exception:
        return []


def _has_open_positions(positions: list[dict], underlying: str) -> bool:
    for p in positions:
        sym = p.get("symbol", "")
        qty = p.get("quantity", p.get("netQty", 0))
        if underlying in sym and int(qty or 0) != 0:
            return True
    return False



def _equity_futures_entry(api_key: str, strategy: dict, spot: float) -> bool:
    """Place initial buy/sell order for equity or futures strategy."""
    underlying = strategy["underlying"]
    stype      = strategy.get("strategy_type", "equity")
    direction  = strategy.get("direction", "LONG")
    is_mcx     = underlying in MCX_UNDERLYINGS

    if stype == "futures":
        # Look up front-month futures contract
        import sqlite3 as _sq
        try:
            conn = _sq.connect("/home/freed/openalgo/db/openalgo.db")
            row  = conn.execute(
                "SELECT symbol FROM symtoken WHERE exchange=? AND symbol LIKE ? "
                "AND instrumenttype='FUT' ORDER BY expiry LIMIT 1",
                ("MCX" if is_mcx else "NFO", f"{underlying}%",)
            ).fetchone()
            conn.close()
            symbol = row[0] if row else underlying + "FUT"
        except Exception as e:
            print(f"[entry] futures symbol lookup error: {e}")
            symbol = underlying + "FUT"
        exchange = "MCX" if is_mcx else "NFO"
        product  = "NRML"
        lots     = strategy.get("lots", 1)
        lot_sizes = {"NIFTY": 75, "BANKNIFTY": 15, "GOLDM": 1, "GOLD": 1, "CRUDEOIL": 100}
        qty = lots * lot_sizes.get(underlying, 1)
    else:
        # Equity (NSE CNC)
        symbol   = underlying
        exchange = "NSE"
        product  = "CNC"
        qty      = strategy.get("qty", 1)

    action = "BUY" if direction == "LONG" else "SELL"
    print(f"[entry] {stype} {action} {qty} {symbol} @ {exchange} {product}")

    if strategy.get("mode") == "sandbox":
        print(f"[entry] SANDBOX mode — skipping actual order")
        # Pre-seed session memory (fresh file) so context_builder filters to equity symbol only
        try:
            import session_memory as _sm_e
            sid = strategy.get("id", "default")
            entry_pos = [{"symbol": symbol, "qty": qty, "avg_price": round(spot, 2)}]
            _sm_e.init_session(underlying, spot, strategy, entry_pos, strategy_id=sid)
            _sm_e.add_owned_symbol(underlying, symbol, sid)
        except Exception as _ee:
            print(f"[entry] session memory pre-seed warning: {_ee}")
        return True

    try:
        r = requests.post(
            f"{OPENALGO_BASE}/api/v1/placeorder",
            json={
                "apikey":    api_key,
                "strategy":  "PositionManager",
                "symbol":    symbol,
                "action":    action,
                "exchange":  exchange,
                "pricetype": "MARKET",
                "product":   product,
                "quantity":  str(qty),
            },
            headers=_oa_headers(),
            timeout=10,
        )
        result = r.json()
        print(f"[entry] order → {result.get('orderid', result)}")
        try:
            import session_memory as _sm_e
            sid = strategy.get("id", "default")
            _sm_e.init_session(underlying, spot, strategy, [{"symbol": symbol, "qty": strategy.get("qty", 1), "avg_price": spot}], strategy_id=sid)
            _sm_e.add_owned_symbol(underlying, symbol, sid)
        except Exception:
            pass
        time.sleep(2)
        return True
    except Exception as e:
        print(f"[entry] ERROR: {e}")
        return False


def _quick_entry(api_key: str, strategy: dict, spot: float) -> bool:
    """Place initial CE + PE SELL orders using premium-target scan."""
    # Import entry_executor helpers — they live in the same agents/ dir
    agents_dir = Path(__file__).parent
    sys.path.insert(0, str(agents_dir))
    from entry_executor import (
        _scan_strike_by_premium,
        _build_option_symbol,
        _calc_dte,
    )

    from entry_executor import LOT_SIZE as _LOT_SIZE, EXCHANGE_FOR as _EXCH, PRODUCT_FOR as _PROD
    underlying = strategy["underlying"]
    expiry     = strategy["expiry"]
    lots       = strategy.get("lots", 1)
    lot_size   = _LOT_SIZE.get(underlying, 65)
    exchange   = _EXCH.get(underlying, "NFO")
    product    = _PROD.get(underlying, "MIS")

    dte = _calc_dte(expiry)
    print(f"[entry] spot={spot:.1f}  expiry={expiry}  DTE={dte}  lots={lots}  exchange={exchange}")

    qty = lots * lot_size

    for opt_type in ("CE", "PE"):
        strike = _scan_strike_by_premium(api_key, underlying, expiry, spot, opt_type, dte)
        symbol = _build_option_symbol(underlying, expiry, strike, opt_type)
        print(f"[entry] SELL {qty} {symbol}")
        try:
            r = requests.post(
                f"{OPENALGO_BASE}/api/v1/placeorder",
                json={
                    "apikey":      api_key,
                    "strategy":    "PositionManager",
                    "symbol":      symbol,
                    "action":      "SELL",
                    "exchange":    exchange,
                    "pricetype":   "MARKET",
                    "product":     product,
                    "quantity":    str(qty),
                },
                headers=_oa_headers(),
                timeout=10,
            )
            result = r.json()
            print(f"[entry] {opt_type} order → {result.get('orderid', result)}")
            # Track owned symbol so context_builder knows this leg belongs to us
            try:
                import session_memory as _sm_e
                _sm_e.add_owned_symbol(underlying, symbol, s.get("id", "default"))
            except Exception:
                pass
        except Exception as e:
            print(f"[entry] ERROR placing {opt_type}: {e}")
            return False

    print("[entry] Both legs submitted. Waiting 3s for fills...")
    time.sleep(3)
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--id", required=True, help="Strategy ID from strategies.json")
    args = parser.parse_args()

    # Load strategy config
    strategies = _load_strategies()
    s = next((x for x in strategies if x["id"] == args.id), None)
    if not s:
        sys.exit(f"ERROR: Strategy '{args.id}' not found in {STRATEGIES_FILE}")

    api_key = os.environ.get("OPENALGO_API_KEY", "")
    if not api_key:
        sys.exit("ERROR: OPENALGO_API_KEY not set")

    underlying = s["underlying"]
    print(f"[start_strategy] Starting: {s['name']} ({underlying})")

    stype = s.get("strategy_type", "options")

    # Step 1: Check for existing positions / handle entry per strategy type
    positions = _get_positions(api_key)
    if _has_open_positions(positions, underlying):
        print(f"[start_strategy] Open positions found for {underlying} — skipping entry")
        entry_price = _get_spot(api_key, underlying)
    elif stype in ("equity", "futures"):
        # Equity/futures: place directional entry order then hand off
        spot = _get_spot(api_key, underlying)
        if spot <= 0:
            sys.exit("ERROR: Could not fetch spot price")
        ok = _equity_futures_entry(api_key, s, spot)
        if not ok:
            sys.exit("ERROR: Equity/futures entry failed")
        entry_price = spot
    else:
        print(f"[start_strategy] No open positions — running options entry")
        spot = _get_spot(api_key, underlying)
        if spot <= 0:
            sys.exit("ERROR: Could not fetch spot price")
        ok = _quick_entry(api_key, s, spot)
        if not ok:
            sys.exit("ERROR: Entry failed — not starting position manager")
        entry_price = spot

    if entry_price <= 0:
        entry_price = _get_spot(api_key, underlying) or 23000.0

    # Step 2: exec into position_manager
    python    = sys.executable
    pm_path   = str(Path(__file__).parent / "position_manager.py")
    cycle_secs = s.get("cycle_secs", 300)
    stype = s.get("strategy_type", "options")

    pm_args = [
        python, pm_path,
        "--underlying",   underlying,
        "--strategy-id",  args.id,
        "--strategy-type", stype,
        "--target",       str(s.get("target", 5000)),
        "--max-loss",     str(s.get("max_loss", -8000)),
        "--entry-price",  str(entry_price),
        "--strategy",     s.get("strategy", "short_strangle"),
        "--cycle-secs",   str(cycle_secs),
    ]

    if stype == "options" and s.get("expiry"):
        pm_args += ["--expiry", s["expiry"]]
    if s.get("direction"):
        pm_args += ["--direction", s["direction"]]
    if s.get("qty"):
        pm_args += ["--qty", str(s["qty"])]
    if s.get("lots"):
        pm_args += ["--lots", str(s["lots"])]
    if s.get("target_price"):
        pm_args += ["--target-price", str(s["target_price"])]
    if s.get("stop_loss_price"):
        pm_args += ["--stop-loss-price", str(s["stop_loss_price"])]
    if s.get("trailing_stop_pct"):
        pm_args += ["--trailing-stop-pct", str(s["trailing_stop_pct"])]

    print(f"[start_strategy] Handing off to position_manager: {' '.join(pm_args[2:])}")
    os.execv(python, pm_args)


if __name__ == "__main__":
    main()
