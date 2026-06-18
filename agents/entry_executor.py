"""
entry_executor.py — 9:15am IST: reads morning_brief.json, enters the recommended
options strategy via OpenAlgo → Zerodha NFO.

Supported strategies:
  iron_condor      — sell OTM CE + OTM PE, buy further OTM wings (4 legs)
  short_straddle   — sell ATM CE + ATM PE (2 legs)
  bull_put_spread  — sell OTM PE, buy further OTM PE (2 legs)
  bear_call_spread — sell OTM CE, buy further OTM CE (2 legs)

Strike selection is based on:
  - OI walls from the morning brief (ce_wall / pe_wall)
  - ATM ± configurable offset if OI walls are too close/far

HOW TO RUN:
  cd ~/autotrade

  # Dry run — shows legs but places NO orders
  .venv/bin/python agents/entry_executor.py --dry-run

  # Live paper trade (uses Sandbox mode in OpenAlgo)
  .venv/bin/python agents/entry_executor.py

  # Override strategy
  .venv/bin/python agents/entry_executor.py --strategy short_straddle --dry-run

  # Custom brief file
  .venv/bin/python agents/entry_executor.py --brief data/morning_briefs/2026-06-08.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import requests
from pydantic import BaseModel

IST = timezone(timedelta(hours=5, minutes=30))
OPENALGO_BASE = "http://localhost:5000"
BRIEFS_DIR = Path("/home/freed/autotrade/data/morning_briefs")

# Current lot sizes — update when exchange changes them
LOT_SIZE    = {"BANKNIFTY": 15, "NIFTY": 65, "GOLDM": 1}
STRIKE_STEP = {"BANKNIFTY": 100, "NIFTY": 50, "GOLDM": 500}

# MCX commodity underlyings — different exchange, product, and premium scale
MCX_UNDERLYINGS = {"GOLDM", "GOLD", "SILVER", "CRUDEOIL", "NATURALGAS"}
EXCHANGE_FOR    = {u: "MCX" for u in MCX_UNDERLYINGS}
EXCHANGE_FOR.update({"NIFTY": "NFO", "BANKNIFTY": "NFO"})
PRODUCT_FOR     = {u: "NRML" for u in MCX_UNDERLYINGS}
PRODUCT_FOR.update({"NIFTY": "MIS", "BANKNIFTY": "MIS"})

# Spot source for MCX: near-month FUT symbol prefix (e.g. "GOLDM" → query symtoken)
MCX_FUT_PREFIX = {"GOLDM": "GOLDM", "GOLD": "GOLD", "SILVER": "SILVER"}

# Default offsets when OI walls not available (in strike steps)
DEFAULT_SHORT_OFFSET = 5   # sell 5 strikes OTM
DEFAULT_WING_OFFSET = 8    # buy wing 8 strikes OTM (for iron condor)

# ── Premium-target strike selection (empirical rules from 17 sessions) ──────
# Minimum OTM% by DTE — tighter allowed only closer to expiry
MIN_OTM_PCT_BY_DTE = {
    0: 0.007,   # 0 DTE (expiry day): 0.7%
    1: 0.010,   # 1 DTE: 1.0%
    2: 0.012,   # 2 DTE: 1.2%
    3: 0.013,   # 3 DTE: 1.3%
    4: 0.015,   # 4 DTE: 1.5%
}
MIN_OTM_PCT_DEFAULT = 0.025    # 5+ DTE (new weekly): 2.5%

MINIMUM_ENTRY_PREMIUM = 20.0   # skip any strike with LTP below this (NFO)
TARGET_PREMIUM_MAX    = 75.0   # stop going tighter once LTP exceeds this (NFO)
SKIP_DAY_ALL_BELOW    = 10.0   # warn if every scanned strike < this (churn day)

# MCX premium targets are in Rs./lot (lot=1 for GOLDM = 10g contract)
MCX_MIN_PREMIUM   = 100.0   # Rs. per lot — skip strikes below this
MCX_MAX_PREMIUM   = 1500.0  # Rs. per lot — go further OTM if above this
MCX_SKIP_BELOW    = 30.0    # Rs. per lot — churn warning threshold


# ---------------------------------------------------------------------------
# Leg definition
# ---------------------------------------------------------------------------

class Leg(BaseModel):
    symbol: str
    action: str          # BUY or SELL
    quantity: int        # in lots × lot_size = actual qty
    exchange: str = "NFO"
    product: str = "MIS"
    price_type: str = "MARKET"
    price: float = 0.0
    role: str = ""       # "short_ce", "short_pe", "wing_ce", "wing_pe"


# ---------------------------------------------------------------------------
# OpenAlgo helpers
# ---------------------------------------------------------------------------

def _headers(api_key: str) -> dict:
    return {"x-api-key": api_key, "Content-Type": "application/json"}


def _get_spot_mcx(api_key: str, underlying: str) -> float:
    """Get MCX spot price from the nearest-expiry FUT contract."""
    import sqlite3 as _sqlite3
    db_path = "/home/freed/openalgo/db/openalgo.db"
    try:
        conn = _sqlite3.connect(db_path)
        cur  = conn.cursor()
        prefix = MCX_FUT_PREFIX.get(underlying, underlying)
        cur.execute(
            "SELECT symbol FROM symtoken "
            "WHERE exchange='MCX' AND symbol LIKE ? AND instrumenttype='FUT' "
            "ORDER BY expiry LIMIT 1",
            (f"{prefix}%",)
        )
        row = conn.execute(
            "SELECT symbol FROM symtoken "
            "WHERE exchange='MCX' AND symbol LIKE ? AND instrumenttype='FUT' "
            "ORDER BY expiry LIMIT 1",
            (f"{prefix}%",)
        ).fetchone()
        conn.close()
        if not row:
            print(f"[entry] WARNING: no MCX FUT found for {underlying}")
            return 0.0
        fut_sym = row[0]
        print(f"[entry] MCX spot via {fut_sym}")
    except Exception as e:
        print(f"[entry] DB error getting MCX FUT: {e}")
        return 0.0

    try:
        resp = requests.post(
            f"{OPENALGO_BASE}/api/v1/quotes",
            json={"apikey": api_key, "symbol": fut_sym, "exchange": "MCX"},
            headers=_headers(api_key),
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        ltp  = data.get("ltp") or data.get("data", {}).get("ltp", 0)
        return float(ltp)
    except Exception as e:
        print(f"[entry] MCX spot fetch error: {e}")
        return 0.0


def _get_spot(api_key: str, underlying: str) -> float:
    if underlying in MCX_UNDERLYINGS:
        return _get_spot_mcx(api_key, underlying)
    resp = requests.post(
        f"{OPENALGO_BASE}/api/v1/quotes",
        json={"apikey": api_key, "symbol": underlying, "exchange": "NSE_INDEX"},
        headers=_headers(api_key),
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    ltp = data.get("ltp") or data.get("data", {}).get("ltp", 0)
    return float(ltp)


def _place_order(api_key: str, leg: Leg) -> dict:
    payload = {
        "apikey": api_key,
        "strategy": "EntryExecutor",
        "symbol": leg.symbol,
        "action": leg.action,
        "exchange": leg.exchange,
        "pricetype": leg.price_type,
        "product": leg.product,
        "quantity": str(leg.quantity),
    }
    if leg.price_type == "LIMIT" and leg.price:
        payload["price"] = str(leg.price)

    resp = requests.post(
        f"{OPENALGO_BASE}/api/v1/placeorder",
        json=payload,
        headers=_headers(api_key),
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Symbol builder
# ---------------------------------------------------------------------------

DB_PATH = Path("/home/freed/openalgo/db/openalgo.db")


def _nth_expiry_from_db(underlying: str, offset: int = 0) -> str:
    """
    Returns the Nth upcoming expiry (chronological) from the DB.

    offset=0 → nearest future expiry (skipping today if expiry falls today)
    offset=1 → one after that (upcoming+1)
    offset=2 → two after that, etc.

    Today's expiry is always skipped — options expiring today have no time value.
    DB format: DD-MON-YY (e.g. "16-JUN-26") → returns "2026-06-16"
    """
    import sqlite3
    today_str = datetime.now(IST).strftime("%Y-%m-%d")
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cur = conn.cursor()
        cur.execute(
            "SELECT DISTINCT expiry FROM symtoken "
            "WHERE exchange='NFO' AND symbol LIKE ? "
            "ORDER BY expiry",
            (f"{underlying}%CE",),
        )
        rows = cur.fetchall()
        conn.close()

        # Parse all, filter to strictly future (skip today), sort chronologically
        future = []
        for (raw,) in rows:
            try:
                dt = datetime.strptime(raw, "%d-%b-%y")
                if dt.strftime("%Y-%m-%d") > today_str:
                    future.append(dt)
            except ValueError:
                pass
        future.sort()

        if offset < len(future):
            return future[offset].strftime("%Y-%m-%d")
    except Exception:
        pass

    # Fallback: approximate based on offset weeks from now
    base = datetime.now(IST) + timedelta(weeks=offset + 1)
    # Snap to nearest Thursday
    days_to_thu = (3 - base.weekday()) % 7
    return (base + timedelta(days=days_to_thu)).strftime("%Y-%m-%d")


# Keep backward-compatible alias
def _nearest_expiry_from_db(underlying: str) -> str:
    return _nth_expiry_from_db(underlying, offset=0)


def _build_option_symbol(underlying: str, expiry_str: str, strike: int, opt_type: str) -> str:
    """
    Returns OpenAlgo NFO symbol. Format: {UNDERLYING}{DD}{MON}{YY}{STRIKE}{TYPE}
    Example: BANKNIFTY30JUN2654000CE  (NOT 26JUN25 — always use YYYY-MM-DD from DB)
    DB expiry "30-JUN-26" → code "30JUN26" → symbol "BANKNIFTY30JUN2654000CE"
    """
    dt = datetime.strptime(expiry_str, "%Y-%m-%d")
    exp_code = dt.strftime("%d%b%y").upper()  # "30JUN26"
    return f"{underlying}{exp_code}{strike}{opt_type}"


def _round_to_step(value: float, step: int) -> int:
    return round(value / step) * step


def _calc_dte(expiry_str: str) -> int:
    """Integer calendar days until expiry from today IST."""
    expiry_dt = datetime.strptime(expiry_str, "%Y-%m-%d")
    today = datetime.now(IST).replace(tzinfo=None)
    return max((expiry_dt - today).days, 0)


def _get_min_otm_pct(dte: int) -> float:
    return MIN_OTM_PCT_BY_DTE.get(dte, MIN_OTM_PCT_DEFAULT)


def _get_option_ltp(api_key: str, symbol: str, exchange: str = "NFO") -> float:
    """Returns LTP for an option symbol on the given exchange. Returns 0.0 on failure."""
    try:
        resp = requests.post(
            f"{OPENALGO_BASE}/api/v1/quotes",
            json={"apikey": api_key, "symbol": symbol, "exchange": exchange},
            headers=_headers(api_key),
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()
        return float(data.get("ltp") or data.get("data", {}).get("ltp", 0))
    except Exception:
        return 0.0


def _scan_strike_by_premium(
    api_key: str,
    underlying: str,
    expiry_str: str,
    spot: float,
    opt_type: str,   # "CE" or "PE"
    dte: int,
) -> int:
    """
    Finds the best strike for a short option sell using empirical premium-target rules.
    Works for both NFO (NIFTY/BANKNIFTY) and MCX (GOLDM) underlyings.
    """
    exchange = EXCHANGE_FOR.get(underlying, "NFO")
    is_mcx   = (exchange == "MCX")
    step     = STRIKE_STEP.get(underlying, 50)
    atm      = _round_to_step(spot, step)
    min_otm_pct = _get_min_otm_pct(dte) if not is_mcx else max(_get_min_otm_pct(dte), 0.015)

    min_premium  = MCX_MIN_PREMIUM  if is_mcx else MINIMUM_ENTRY_PREMIUM
    max_premium  = MCX_MAX_PREMIUM  if is_mcx else TARGET_PREMIUM_MAX
    skip_below   = MCX_SKIP_BELOW   if is_mcx else SKIP_DAY_ALL_BELOW

    min_otm_pts = int(spot * min_otm_pct)
    if opt_type == "CE":
        start = atm + max(_round_to_step(min_otm_pts, step), step)
        direction = 1
    else:
        start = atm - max(_round_to_step(min_otm_pts, step), step)
        direction = -1

    best_fallback = start
    max_scan = 20

    for i in range(max_scan):
        strike = start + direction * i * step
        sym = _build_option_symbol(underlying, expiry_str, strike, opt_type)
        ltp = _get_option_ltp(api_key, sym, exchange)

        if ltp <= 0:
            continue

        if ltp > max_premium:
            continue   # too tight, go further OTM

        if ltp >= min_premium:
            return strike

        print(f"  !! {opt_type} scan: {strike} LTP={ltp:.2f} below minimum — stopping scan")
        if ltp < skip_below:
            print(f"  !! WARNING: {opt_type} premiums near zero — possible churn day (DTE={dte})")
        break

    print(f"  !! {opt_type} premium scan fallback to default offset (DTE={dte})")
    return best_fallback


# ---------------------------------------------------------------------------
# Strategy leg builders
# ---------------------------------------------------------------------------

def _build_iron_condor(
    underlying: str, expiry: str, spot: float,
    api_key: str,
    lots: int,
) -> list[Leg]:
    """
    Short CE + short PE + long wings. Strikes selected by premium target,
    not OI walls. Wings are placed DEFAULT_WING_OFFSET steps beyond short legs.
    """
    step = STRIKE_STEP.get(underlying, 100)
    dte  = _calc_dte(expiry)

    short_ce = _scan_strike_by_premium(api_key, underlying, expiry, spot, "CE", dte)
    short_pe = _scan_strike_by_premium(api_key, underlying, expiry, spot, "PE", dte)
    wing_ce  = short_ce + DEFAULT_WING_OFFSET * step
    wing_pe  = short_pe - DEFAULT_WING_OFFSET * step

    qty = lots * LOT_SIZE.get(underlying, 15)

    return [
        Leg(symbol=_build_option_symbol(underlying, expiry, short_ce, "CE"),
            action="SELL", quantity=qty, role="short_ce"),
        Leg(symbol=_build_option_symbol(underlying, expiry, short_pe, "PE"),
            action="SELL", quantity=qty, role="short_pe"),
        Leg(symbol=_build_option_symbol(underlying, expiry, wing_ce, "CE"),
            action="BUY",  quantity=qty, role="wing_ce"),
        Leg(symbol=_build_option_symbol(underlying, expiry, wing_pe, "PE"),
            action="BUY",  quantity=qty, role="wing_pe"),
    ]


def _build_short_strangle(
    underlying: str, expiry: str, spot: float,
    api_key: str,
    lots: int,
) -> list[Leg]:
    """
    Short strangle — sell OTM CE + sell OTM PE, no wing protection.

    Strike selection: premium-target scan (empirical rules from 17 sessions).
    Target: find the strike where LTP is in [20, 75] AND OTM% >= min for DTE.
    This ensures meaningful theta per leg without getting recklessly tight.
    """
    dte      = _calc_dte(expiry)
    exchange = EXCHANGE_FOR.get(underlying, "NFO")
    product  = PRODUCT_FOR.get(underlying, "MIS")
    sell_ce  = _scan_strike_by_premium(api_key, underlying, expiry, spot, "CE", dte)
    sell_pe  = _scan_strike_by_premium(api_key, underlying, expiry, spot, "PE", dte)
    qty      = lots * LOT_SIZE.get(underlying, 75)

    return [
        Leg(symbol=_build_option_symbol(underlying, expiry, sell_ce, "CE"),
            action="SELL", quantity=qty, exchange=exchange, product=product, role="short_ce"),
        Leg(symbol=_build_option_symbol(underlying, expiry, sell_pe, "PE"),
            action="SELL", quantity=qty, exchange=exchange, product=product, role="short_pe"),
    ]


def _build_short_straddle(
    underlying: str, expiry: str, spot: float,
    max_pain: Optional[int],
    lots: int,
) -> list[Leg]:
    step = STRIKE_STEP.get(underlying, 100)
    # Sell at max pain if available (classic straddle), else ATM
    sell_strike = max_pain if max_pain else _round_to_step(spot, step)
    qty = lots * LOT_SIZE.get(underlying, 15)

    return [
        Leg(symbol=_build_option_symbol(underlying, expiry, sell_strike, "CE"),
            action="SELL", quantity=qty, role="short_ce"),
        Leg(symbol=_build_option_symbol(underlying, expiry, sell_strike, "PE"),
            action="SELL", quantity=qty, role="short_pe"),
    ]


def _build_bull_put_spread(
    underlying: str, expiry: str, spot: float,
    pe_wall: Optional[int],
    lots: int,
) -> list[Leg]:
    step = STRIKE_STEP.get(underlying, 100)
    atm = _round_to_step(spot, step)
    # Sell PE at or slightly above pe_wall (2 strikes closer to ATM)
    sell_pe = (pe_wall + 2 * step) if pe_wall else (atm - DEFAULT_SHORT_OFFSET * step)
    buy_pe = sell_pe - DEFAULT_WING_OFFSET * step
    qty = lots * LOT_SIZE.get(underlying, 15)

    return [
        Leg(symbol=_build_option_symbol(underlying, expiry, sell_pe, "PE"),
            action="SELL", quantity=qty, role="short_pe"),
        Leg(symbol=_build_option_symbol(underlying, expiry, buy_pe, "PE"),
            action="BUY",  quantity=qty, role="wing_pe"),
    ]


def _build_bear_call_spread(
    underlying: str, expiry: str, spot: float,
    ce_wall: Optional[int],
    lots: int,
) -> list[Leg]:
    step = STRIKE_STEP.get(underlying, 100)
    atm = _round_to_step(spot, step)
    # Sell CE at or slightly below ce_wall (2 strikes closer to ATM)
    sell_ce = (ce_wall - 2 * step) if ce_wall else (atm + DEFAULT_SHORT_OFFSET * step)
    buy_ce = sell_ce + DEFAULT_WING_OFFSET * step
    qty = lots * LOT_SIZE.get(underlying, 15)

    return [
        Leg(symbol=_build_option_symbol(underlying, expiry, sell_ce, "CE"),
            action="SELL", quantity=qty, role="short_ce"),
        Leg(symbol=_build_option_symbol(underlying, expiry, buy_ce, "CE"),
            action="BUY",  quantity=qty, role="wing_ce"),
    ]


VALID_STRATEGIES = {
    "iron_condor", "short_strangle", "short_straddle",
    "bull_put_spread", "bear_call_spread", "hold",
}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def execute_entry(
    brief_path: Optional[str] = None,
    strategy_override: Optional[str] = None,
    lots: int = 1,
    dry_run: bool = True,
    expiry_offset: int = 0,
) -> dict:
    """
    Reads today's morning brief and enters the recommended strategy.
    Returns a dict with the legs placed (or would-be placed on dry_run).
    """
    # Load env
    env_path = Path("/home/freed/autotrade/.env")
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip())

    api_key = os.environ.get("OPENALGO_API_KEY", "")
    if not api_key:
        sys.exit("OPENALGO_API_KEY not set in ~/autotrade/.env")

    # Load morning brief
    if brief_path is None:
        today = datetime.now(IST).strftime("%Y-%m-%d")
        brief_path = BRIEFS_DIR / f"{today}.json"
    brief = json.loads(Path(brief_path).read_text())

    underlying = brief.get("underlying", "BANKNIFTY")
    strategy = strategy_override or brief.get("strategy_recommendation", "iron_condor")
    oi = brief.get("oi_analysis") or {}

    # Live spot price — fetched fresh at entry time (9:08 preview or 9:15 live)
    spot = _get_spot(api_key, underlying)

    # expiry_offset overrides brief expiry — used to select upcoming+1, +2, etc.
    expiry = _nth_expiry_from_db(underlying, offset=expiry_offset)
    dte = _calc_dte(expiry)
    max_pain = oi.get("max_pain")

    print(f"\n{'='*55}")
    print(f"Entry Executor — {datetime.now(IST).strftime('%H:%M IST')}")
    print(f"  Underlying : {underlying}  Spot: {spot:.0f}")
    print(f"  Expiry     : {expiry}  (DTE={dte})")
    print(f"  Strategy   : {strategy}")
    print(f"  Min OTM%%  : {_get_min_otm_pct(dte)*100:.1f}%%  |  Premium target: {MINIMUM_ENTRY_PREMIUM:.0f}–{TARGET_PREMIUM_MAX:.0f}")
    print(f"  Lots       : {lots}  ({'DRY RUN' if dry_run else 'LIVE'})")
    print(f"{'='*55}")

    if strategy not in VALID_STRATEGIES:
        print(f"Unknown strategy '{strategy}' — valid: {sorted(VALID_STRATEGIES)}")
        return {}

    if strategy == "hold":
        print("Strategy is HOLD — no entry placed.")
        return {"strategy": "hold", "legs": []}

    # Build legs — all premium-target builders receive api_key for live quote scan
    if strategy == "iron_condor":
        legs = _build_iron_condor(underlying, expiry, spot, api_key, lots)
    elif strategy == "short_strangle":
        legs = _build_short_strangle(underlying, expiry, spot, api_key, lots)
    elif strategy == "short_straddle":
        legs = _build_short_straddle(underlying, expiry, spot, max_pain, lots)
    elif strategy == "bull_put_spread":
        legs = _build_bull_put_spread(underlying, expiry, spot, oi.get("pe_wall"), lots)
    elif strategy == "bear_call_spread":
        legs = _build_bear_call_spread(underlying, expiry, spot, oi.get("ce_wall"), lots)
    else:
        legs = []

    # Display legs
    print(f"\nLegs:")
    for leg in legs:
        print(f"  {leg.action:4s} {leg.quantity:3d} × {leg.symbol:35s}  [{leg.role}]")

    results = []
    if dry_run:
        print("\n[DRY RUN] No orders placed.")
        results = [{"leg": l.model_dump(), "status": "dry_run"} for l in legs]
    else:
        print("\nPlacing orders...")
        for leg in legs:
            try:
                result = _place_order(api_key, leg)
                order_id = result.get("orderid", str(result))
                print(f"  ✓ {leg.symbol} {leg.action} → order {order_id}")
                results.append({"leg": leg.model_dump(), "status": "placed", "orderid": order_id})
            except Exception as e:
                print(f"  ✗ {leg.symbol} FAILED: {e}")
                results.append({"leg": leg.model_dump(), "status": "error", "error": str(e)})

    # Save entry log
    log = {
        "timestamp": datetime.now(IST).isoformat(),
        "underlying": underlying,
        "strategy": strategy,
        "spot_at_entry": spot,
        "expiry": expiry,
        "lots": lots,
        "dry_run": dry_run,
        "legs": results,
        "brief_date": brief.get("date"),
        "oi_summary": oi.get("summary"),
    }
    log_dir = Path("/home/freed/autotrade/data/entry_logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(IST).strftime("%Y-%m-%d")
    log_path = log_dir / f"{today}.json"
    with open(log_path, "w") as f:
        json.dump(log, f, indent=2)
    print(f"\nEntry log saved → {log_path}")

    return log


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="9:15am entry executor")
    parser.add_argument("--brief", help="Path to morning_brief JSON (default: today's)")
    parser.add_argument("--strategy", help="Override strategy recommendation")
    parser.add_argument("--lots", type=int, default=1, help="Number of lots (default: 1)")
    parser.add_argument("--dry-run", action="store_true", help="Show legs without placing orders")
    parser.add_argument("--expiry-offset", type=int, default=0,
                        help="0=nearest upcoming expiry, 1=upcoming+1, 2=upcoming+2 (default: 0)")
    args = parser.parse_args()

    execute_entry(
        brief_path=args.brief,
        strategy_override=args.strategy,
        lots=args.lots,
        dry_run=args.dry_run,
        expiry_offset=args.expiry_offset,
    )
