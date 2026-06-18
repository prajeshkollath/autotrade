"""
context_builder.py — Builds the 15-min ContextSnapshot for the intraday agent.

Data sources:
  - OpenAlgo REST API (localhost:5000): positions, live quotes, OI from Zerodha feed
  - yfinance: VIX (^INDIAVIX)
  - opengreeks: Black-Scholes delta/theta/vega per leg
  - Morning brief JSON: from TradingAgents 6am run

HOW TO RUN (standalone test):
  cd ~/autotrade
  .venv/bin/python agents/context_builder.py
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import requests

sys.path.insert(0, str(Path(__file__).parent))
from goal_schema import ContextSnapshot, Goal, PositionSnapshot

IST = timezone(timedelta(hours=5, minutes=30))

# Intraday high/low — module-level, reset on agent restart each day
_intraday_high: float = 0.0
_intraday_low: float = float('inf')

def _update_intraday(spot: float) -> None:
    global _intraday_high, _intraday_low
    if _intraday_high == 0.0:
        _intraday_high = spot
    if _intraday_low == float('inf'):
        _intraday_low = spot
    _intraday_high = max(_intraday_high, spot)
    _intraday_low  = min(_intraday_low,  spot)

OPENALGO_BASE = "http://localhost:5000"
DATA_DIR = Path("/home/freed/autotrade/data")
OI_SNAPSHOT_PATH = Path("/tmp/oi_morning_snapshot.json")

# BANKNIFTY option strikes move in multiples of 100; NIFTY in 50
STRIKE_STEP = {"BANKNIFTY": 100, "NIFTY": 50}
# How many strikes above/below ATM to query for PCR
OI_RANGE_STRIKES = 15

# Module-level cache — avoids hammering Zerodha/yfinance on every 1-min cycle
_cache: dict = {}
VIX_CACHE_SECONDS = 300    # refresh VIX every 5 min
OI_CACHE_SECONDS  = 300    # refresh live OI every 5 min


# ---------------------------------------------------------------------------
# OpenAlgo helpers
# ---------------------------------------------------------------------------

def _oa_headers(api_key: str) -> dict:
    return {"x-api-key": api_key, "Content-Type": "application/json"}


def get_positions(api_key: str) -> list[dict]:
    """Returns raw OpenAlgo positions list."""
    # OpenAlgo REST API: positionbook uses POST with apikey in body
    resp = requests.post(
        f"{OPENALGO_BASE}/api/v1/positionbook",
        json={"apikey": api_key},
        headers=_oa_headers(api_key),
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("data", data) if isinstance(data, dict) else data


def get_quote(api_key: str, symbol: str, exchange: str = "NFO") -> dict:
    """Single quote — returns dict with ltp, oi, etc."""
    resp = requests.post(
        f"{OPENALGO_BASE}/api/v1/quotes",
        json={"apikey": api_key, "symbol": symbol, "exchange": exchange},
        headers=_oa_headers(api_key),
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


_MCX_UNDERLYINGS = {"GOLDM", "GOLD", "SILVER", "CRUDEOIL", "NATURALGAS"}


def _get_spot_mcx(api_key: str, underlying: str) -> float:
    """MCX commodity spot via nearest FUT from symtoken DB."""
    import sqlite3 as _sq
    try:
        conn = _sq.connect("/home/freed/openalgo/db/openalgo.db")
        row  = conn.execute(
            "SELECT symbol FROM symtoken WHERE exchange='MCX' AND symbol LIKE ? "
            "AND instrumenttype='FUT' ORDER BY expiry LIMIT 1",
            (f"{underlying}%",)
        ).fetchone()
        conn.close()
        if not row:
            return 0.0
        fut_sym = row[0]
        q = get_quote(api_key, fut_sym, exchange="MCX")
        return float(q.get("ltp", q.get("data", {}).get("ltp", 0)))
    except Exception as e:
        print(f"[context_builder] MCX spot error: {e}")
        return 0.0


def get_spot(api_key: str, underlying: str) -> float:
    """Underlying spot price from Zerodha/MCX via OpenAlgo.
    Correct exchange+symbol pairs (confirmed live 2026-06-09):
      NIFTY       NSE_INDEX
      BANKNIFTY   NSE_INDEX
      SENSEX      BSE_INDEX
      GOLDM/MCX   nearest FUT from symtoken DB
    """
    if underlying.upper() in _MCX_UNDERLYINGS:
        return _get_spot_mcx(api_key, underlying)
    symbol_map = {
        "NIFTY":     ("NIFTY",     "NSE_INDEX"),
        "BANKNIFTY": ("BANKNIFTY", "NSE_INDEX"),
        "SENSEX":    ("SENSEX",    "BSE_INDEX"),
    }
    sym, exch = symbol_map.get(underlying, (underlying, "NSE"))  # equity stocks use NSE
    q = get_quote(api_key, sym, exchange=exch)
    return float(q.get("ltp", q.get("data", {}).get("ltp", 0)))


# ---------------------------------------------------------------------------
# OI + PCR from Zerodha via OpenAlgo
# ---------------------------------------------------------------------------

def _build_option_symbols(underlying: str, spot: float, expiry_str: str) -> list[str]:
    """
    Build ATM±N option symbols in Zerodha NFO format.
    Zerodha format: BANKNIFTY24DEC2451000CE / NIFTY24DEC2424000CE
    expiry_str: YYYY-MM-DD -> converted to DDMmmYY e.g. "24DEC24"
    """
    dt = datetime.strptime(expiry_str, "%Y-%m-%d")
    exp_code = dt.strftime("%d%b%y").upper()  # e.g. 26DEC24

    step = STRIKE_STEP.get(underlying, 100)
    atm = round(spot / step) * step
    strikes = range(atm - OI_RANGE_STRIKES * step, atm + (OI_RANGE_STRIKES + 1) * step, step)

    symbols = []
    for k in strikes:
        symbols.append(f"{underlying}{exp_code}{int(k)}CE")
        symbols.append(f"{underlying}{exp_code}{int(k)}PE")
    return symbols


def fetch_oi_map(api_key: str, underlying: str, spot: float, expiry: str) -> dict[str, int]:
    """
    Returns {symbol: oi} for ATM±N strikes via OpenAlgo/Zerodha.
    Skips symbols that fail (illiquid far OTM).
    """
    symbols = _build_option_symbols(underlying, spot, expiry)
    oi_map: dict[str, int] = {}
    for sym in symbols:
        try:
            q = get_quote(api_key, sym, exchange="NFO")
            oi_val = q.get("oi", q.get("data", {}).get("oi", 0))
            oi_map[sym] = int(oi_val or 0)
        except Exception:
            pass
    return oi_map


def compute_pcr(oi_map: dict[str, int]) -> Optional[float]:
    """PCR = total PE OI / total CE OI."""
    pe_oi = sum(v for k, v in oi_map.items() if k.endswith("PE"))
    ce_oi = sum(v for k, v in oi_map.items() if k.endswith("CE"))
    return pe_oi / ce_oi if ce_oi > 0 else None


def load_or_create_oi_snapshot(api_key: str, underlying: str, spot: float, expiry: str) -> dict[str, int]:
    """
    On first call of the day (9:15am), fetch and store OI snapshot.
    Subsequent calls load the stored snapshot for 'shift since morning' comparison.
    """
    today = datetime.now(IST).strftime("%Y-%m-%d")
    if OI_SNAPSHOT_PATH.exists():
        snap = json.loads(OI_SNAPSHOT_PATH.read_text())
        if snap.get("date") == today:
            return snap.get("oi_map", {})

    # First call — fetch and persist
    oi_map = fetch_oi_map(api_key, underlying, spot, expiry)
    OI_SNAPSHOT_PATH.write_text(json.dumps({"date": today, "oi_map": oi_map}))
    return oi_map


def oi_shift_summary(morning_oi: dict[str, int], current_oi: dict[str, int], underlying_price: float, underlying: str) -> str:
    """
    Returns human-readable summary of significant OI changes since morning.
    Focuses on strikes near ATM (within 5 strikes).
    """
    step = STRIKE_STEP.get(underlying, 100)
    atm = round(underlying_price / step) * step
    near_range = range(atm - 5 * step, atm + 6 * step, step)

    lines = []
    for sym, curr_oi in sorted(current_oi.items()):
        # Parse strike from symbol e.g. BANKNIFTY24DEC2451000CE -> 51000
        m = re.search(r"(\d+)(CE|PE)$", sym)
        if not m:
            continue
        strike = int(m.group(1))
        opt_type = m.group(2)
        if strike not in near_range:
            continue

        morning = morning_oi.get(sym, 0)
        if morning == 0:
            continue
        chg = curr_oi - morning
        pct = chg / morning * 100 if morning else 0
        if abs(pct) >= 10:  # only report ≥10% change
            direction = "buildup" if chg > 0 else "unwinding"
            lines.append(f"  {sym}: {direction} {pct:+.0f}% ({chg:+,})")

    return "\n".join(lines) if lines else "No significant OI shifts near ATM"


# ---------------------------------------------------------------------------
# VIX via yfinance
# ---------------------------------------------------------------------------

def fetch_vix(api_key: str = "") -> Optional[float]:
    """Fetch India VIX from Zerodha via OpenAlgo (NSE_INDEX / INDIAVIX).
    Falls back to yfinance if OpenAlgo unavailable.
    Cached for 5 minutes to avoid API spam on 1-min loop.
    """
    import time
    now = time.monotonic()
    cached = _cache.get("vix")
    if cached and now - cached["ts"] < VIX_CACHE_SECONDS:
        return cached["value"]
    try:
        # Try OpenAlgo first (Zerodha live feed)
        _k = api_key or os.environ.get("OPENALGO_API_KEY", "")
        if _k:
            try:
                q = get_quote(_k, "INDIAVIX", exchange="NSE_INDEX")
                v = q.get("ltp", q.get("data", {}).get("ltp"))
                if v:
                    _cache["vix"] = {"value": float(v), "ts": now}
                    return float(v)
            except Exception:
                pass
        # Fallback to yfinance
        import yfinance as yf
        ticker = yf.Ticker("^INDIAVIX")
        hist = ticker.history(period="1d", interval="5m")
        if not hist.empty:
            val = float(hist["Close"].iloc[-1])
            _cache["vix"] = {"ts": now, "value": val}
            return val
    except Exception:
        pass
    return cached["value"] if cached else None


def fetch_oi_map_cached(api_key: str, underlying: str, spot: float, expiry: str) -> dict[str, int]:
    """Live OI map — cached for 5 minutes so 1-min loop doesn't flood Zerodha."""
    import time
    key = f"oi_{underlying}_{expiry}"
    now = time.monotonic()
    cached = _cache.get(key)
    if cached and now - cached["ts"] < OI_CACHE_SECONDS:
        return cached["value"]
    oi = fetch_oi_map(api_key, underlying, spot, expiry)
    _cache[key] = {"ts": now, "value": oi}
    return oi


# ---------------------------------------------------------------------------
# Greeks via opengreeks
# ---------------------------------------------------------------------------

def _parse_nfo_symbol(symbol: str) -> Optional[dict]:
    """
    Parse Zerodha NFO symbol into components.
    Format: BANKNIFTY24DEC2451000CE / NIFTY24DEC2424000CE / GOLDM26JUN26157000CE
    Returns: {underlying, expiry_str, strike, option_type} or None
    """
    m = re.match(
        r"^(BANKNIFTY|NIFTY|GOLDM|GOLD|SILVER|CRUDEOIL|NATURALGAS)(\d{2})([A-Z]{3})(\d{2})(\d+)(CE|PE)$",
        symbol.upper(),
    )
    if not m:
        return None
    underlying, dd, mon, yy, strike, opt_type = m.groups()
    try:
        expiry = datetime.strptime(f"{dd}{mon}{yy}", "%d%b%y")
        return {
            "underlying": underlying,
            "expiry": expiry,
            "strike": int(strike),
            "option_type": "C" if opt_type == "CE" else "P",
        }
    except ValueError:
        return None


def calc_greeks(symbol: str, qty: int, ltp: float, spot: float, risk_free: float = 0.065) -> dict:
    """
    Returns {delta, theta, vega, iv} for one position leg.
    Uses opengreeks.black_scholes with proper IV inversion.
    qty: positive = long, negative = short (sign convention)
    """
    try:
        from opengreeks.black_scholes import (
            implied_volatility as _iv,
            black_scholes as _bs,
            delta as _delta,
            theta as _theta,
            vega as _vega,
        )
        parsed = _parse_nfo_symbol(symbol)
        if not parsed:
            return {"delta": 0.0, "theta": 0.0, "vega": 0.0, "iv": 0.0}

        now = datetime.now(IST).replace(tzinfo=None)
        T = max((parsed["expiry"] - now).total_seconds() / (365.25 * 24 * 3600), 1e-6)
        flag = parsed["option_type"].lower()   # 'c' or 'p'
        K    = float(parsed["strike"])

        # Proper IV inversion via Newton-Raphson (opengreeks Rust backend)
        iv = 0.20
        if ltp > 0 and spot > 0:
            try:
                iv = float(_iv(ltp, spot, K, T, risk_free, flag))
                iv = max(0.01, min(iv, 5.0))
            except Exception:
                pass

        sign = qty / abs(qty) if qty != 0 else 1
        return {
            "delta": float(_delta(flag, spot, K, T, risk_free, iv)) * sign,
            "theta": float(_theta(flag, spot, K, T, risk_free, iv)) * sign,
            "vega":  float(_vega( flag, spot, K, T, risk_free, iv)) * sign,
            "iv":    iv,
        }
    except Exception:
        return {"delta": 0.0, "theta": 0.0, "vega": 0.0, "iv": 0.0}


# ---------------------------------------------------------------------------
# Main context builder
# ---------------------------------------------------------------------------

def build_context(goal: Goal, api_key: str, entry_underlying_price: float) -> ContextSnapshot:
    """
    Assembles a full ContextSnapshot. Called every 15 min by position_manager.
    entry_underlying_price: spot at the time positions were entered (for move calc).
    """
    now_ist = datetime.now(IST)
    ts = now_ist.strftime("%H:%M IST")

    # --- Positions ---
    raw_positions = get_positions(api_key)
    spot = get_spot(api_key, goal.underlying)
    # Multi-strategy isolation: filter to symbols owned by this strategy_id
    _strat_id = getattr(goal, "strategy_id", "default")
    try:
        import session_memory as _smf
        _owned = set(_smf.get_owned_symbols(goal.underlying, _strat_id))
    except Exception:
        _owned = set()
    if _owned:
        raw_positions = [p for p in raw_positions
                         if (p.get("symbol") or p.get("tradingsymbol","")).upper() in _owned]

    position_snaps: list[PositionSnapshot] = []
    total_pnl = 0.0

    # Days to expiry — None for equity/futures (no expiry)
    session_dte = None
    if goal.expiry:
        try:
            expiry_dt_naive = datetime.strptime(goal.expiry, "%Y-%m-%d")
            session_dte = max((expiry_dt_naive - now_ist.replace(tzinfo=None)).days, 0)
        except Exception:
            pass

    for p in raw_positions:
        sym = p.get("symbol") or p.get("tradingsymbol", "")
        qty_raw = int(p.get("quantity") or p.get("netqty") or 0)
        if qty_raw == 0:
            continue

        avg = float(p.get("average_price") or p.get("averageprice") or 0)
        ltp = float(p.get("ltp") or p.get("last_price") or 0)
        pnl = float(p.get("pnl") or p.get("unrealised") or 0)
        total_pnl += pnl

        greeks = calc_greeks(sym, qty_raw, ltp, spot)

        # ── Risk fields: OTM%, premium ratio ─────────────────────────────
        otm_pct = None
        premium_ratio = None
        parsed = _parse_nfo_symbol(sym)
        if parsed and spot > 0:
            strike = parsed["strike"]
            opt_type = parsed["option_type"]  # "C" or "P"
            if opt_type == "C":
                otm_pct = (strike - spot) / spot   # positive = OTM
            else:
                otm_pct = (spot - strike) / spot   # positive = OTM
            otm_pct = max(otm_pct, 0.0)            # clamp — if ITM just show 0

        if avg > 0 and ltp > 0:
            premium_ratio = ltp / avg    # >1.0 = option got more expensive (bad for short)

        position_snaps.append(PositionSnapshot(
            symbol=sym,
            product=p.get("product", "MIS"),
            qty=qty_raw,
            avg_price=avg,
            ltp=ltp,
            pnl=pnl,
            delta=greeks["delta"],
            theta=greeks["theta"],
            vega=greeks["vega"],
            otm_pct=round(otm_pct, 4) if otm_pct is not None else None,
            premium_ratio=round(premium_ratio, 3) if premium_ratio is not None else None,
            dte=session_dte,
        ))

    net_delta = sum(p.delta or 0 for p in position_snaps)
    net_theta = sum(p.theta or 0 for p in position_snaps)
    net_vega = sum(p.vega or 0 for p in position_snaps)

    # --- Time to expiry ---
    tte_hours = 0.0
    if goal.expiry:
        try:
            expiry_dt = datetime.strptime(goal.expiry, "%Y-%m-%d").replace(
                hour=15, minute=30, tzinfo=IST)
            tte_hours = max((expiry_dt - now_ist).total_seconds() / 3600, 0)
        except Exception:
            pass

    # --- OI + PCR from Zerodha via OpenAlgo ---
    pcr: Optional[float] = None
    pcr_trend: Optional[str] = None
    oi_summary = None
    try:
        morning_oi = load_or_create_oi_snapshot(api_key, goal.underlying, spot, goal.expiry) if goal.expiry else {}
        current_oi = fetch_oi_map_cached(api_key, goal.underlying, spot, goal.expiry) if goal.expiry else {}
        pcr = compute_pcr(current_oi)
        morning_pcr = compute_pcr(morning_oi)
        if pcr and morning_pcr:
            if pcr > morning_pcr * 1.03:
                pcr_trend = "rising"
            elif pcr < morning_pcr * 0.97:
                pcr_trend = "falling"
            else:
                pcr_trend = "flat"
        oi_summary = oi_shift_summary(morning_oi, current_oi, spot, goal.underlying)
    except Exception as e:
        oi_summary = f"OI fetch failed: {e}"

    # --- VIX ---
    vix = fetch_vix(api_key)

    # --- Morning brief ---
    morning_brief = None
    if goal.morning_brief_path:
        try:
            morning_brief = json.loads(Path(goal.morning_brief_path).read_text())
        except Exception:
            pass

    move_pts = spot - entry_underlying_price
    move_pct = (move_pts / entry_underlying_price * 100) if entry_underlying_price else 0

    _update_intraday(spot)

    return ContextSnapshot(
        timestamp_ist=ts,
        current_pnl=total_pnl,
        net_delta=net_delta,
        net_theta=net_theta,
        net_vega=net_vega,
        underlying_price=spot,
        underlying_move_pts=move_pts,
        underlying_move_pct=move_pct,
        vix_now=vix,
        pcr_now=round(pcr, 3) if pcr else None,
        pcr_trend=pcr_trend,
        time_to_expiry_hours=round(tte_hours, 2),
        positions=position_snaps,
        oi_shift_summary=oi_summary,
        morning_brief=morning_brief,
        intraday_high=_intraday_high if _intraday_high > 0 else None,
        intraday_low=_intraday_low  if _intraday_low  < float('inf') else None,
    )


# ---------------------------------------------------------------------------
# Standalone test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Quick smoke test — reads OPENALGO_API_KEY from env or .env file
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
        print("Set OPENALGO_API_KEY in ~/autotrade/.env to test")
        sys.exit(1)

    from goal_schema import Goal
    goal = Goal(
        underlying="BANKNIFTY",
        target_profit=8000,
        max_loss=-6000,
        expiry="2024-12-26",
        style="conservative",
    )

    ctx = build_context(goal, api_key, entry_underlying_price=51000)
    print(json.dumps(ctx.model_dump(), indent=2, default=str))
