"""
oi_analyst.py — Stage 7: Custom OI Analyst for Indian index options.

Reads Zerodha option chain via OpenAlgo, computes:
  - PCR (Put/Call Ratio) with trend vs morning snapshot
  - Max Pain strike
  - OI walls (strongest CE resistance, PE support)
  - Expected intraday range (from OI walls)
  - Strategy recommendation (iron_condor / short_straddle /
    bull_put_spread / bear_call_spread / hold)

Called from morning_brief.py at 6am and produces the `oi_analysis`
block that is injected into the final morning_brief.json.

HOW TO RUN (standalone test):
  cd ~/autotrade
  .venv/bin/python agents/oi_analyst.py --underlying BANKNIFTY
"""
from __future__ import annotations

import os
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import requests
from pydantic import BaseModel

IST = timezone(timedelta(hours=5, minutes=30))
OPENALGO_BASE = "http://localhost:5000"

# Lot sizes (update if exchange changes them)
LOT_SIZE = {"BANKNIFTY": 15, "NIFTY": 75}

# Strike step sizes
STRIKE_STEP = {"BANKNIFTY": 100, "NIFTY": 50}

# How many strikes above/below ATM to query
OI_RANGE = 20


# ---------------------------------------------------------------------------
# Output schema
# ---------------------------------------------------------------------------

class OIAnalysis(BaseModel):
    underlying: str
    expiry: str                        # YYYY-MM-DD
    spot: float
    atm_strike: int

    pcr: float                         # current PCR
    pcr_trend: str                     # rising / falling / flat
    pcr_morning: Optional[float]       # PCR at market open snapshot

    max_pain: int
    ce_wall: int                       # strike with highest CE OI
    pe_wall: int                       # strike with highest PE OI
    ce_wall_oi: int
    pe_wall_oi: int

    expected_range_low: int
    expected_range_high: int
    expected_range_str: str            # e.g. "54500-55500"

    total_ce_oi: int
    total_pe_oi: int

    strategy_recommendation: str       # iron_condor | short_straddle |
                                       # bull_put_spread | bear_call_spread | hold
    strategy_reason: str

    summary: str                       # 2-3 sentence human-readable brief


# ---------------------------------------------------------------------------
# Zerodha / OpenAlgo helpers
# ---------------------------------------------------------------------------

def _headers(api_key: str) -> dict:
    return {"x-api-key": api_key, "Content-Type": "application/json"}


def _get_spot(api_key: str, underlying: str) -> float:
    # Index symbols live on NSE_INDEX exchange in OpenAlgo
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


DB_PATH = Path("/home/freed/openalgo/db/openalgo.db")


def _nearest_expiry_from_db(underlying: str, offset: int = 0) -> tuple[str, str]:
    """
    Returns the Nth upcoming expiry for the underlying.
    offset=0 → nearest future expiry (today's expiry skipped — no time value)
    offset=1 → upcoming+1 (next after nearest)

    Returns (expiry_yyyy_mm_dd, expiry_code_for_symbol) e.g. ("2026-06-23", "23JUN26")
    DB stores expiry as "30-JUN-26". Symbol uses "30JUN26" (no hyphens).
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
    except Exception:
        rows = []

    # Parse, skip today, sort chronologically, pick by offset
    future = []
    for (raw_expiry,) in rows:
        try:
            dt = datetime.strptime(raw_expiry, "%d-%b-%y")
            if dt.strftime("%Y-%m-%d") > today_str:   # strictly future
                future.append(dt)
        except ValueError:
            continue
    future.sort()

    if offset < len(future):
        dt = future[offset]
        return dt.strftime("%Y-%m-%d"), dt.strftime("%d%b%y").upper()

    # Fallback if DB unavailable
    now = datetime.now(IST)
    # Last Thursday of current month
    dt = now.replace(day=28) + timedelta(days=4)
    dt = dt - timedelta(days=dt.weekday() + 1 + 1)  # rough last Thursday
    return dt.strftime("%Y-%m-%d"), dt.strftime("%d%b%y").upper()


def _build_symbols(underlying: str, atm: int, expiry_code: str) -> tuple[list[str], list[int]]:
    """Returns (symbols, strikes). Keep strikes separate to avoid re-parsing."""
    step = STRIKE_STEP.get(underlying, 100)
    symbols = []
    strikes = []
    for i in range(-OI_RANGE, OI_RANGE + 1):
        strike = atm + i * step
        strikes.append(strike)
        symbols.append(f"{underlying}{expiry_code}{strike}CE")
        symbols.append(f"{underlying}{expiry_code}{strike}PE")
    return symbols, strikes


def _fetch_oi_map(api_key: str, symbols: list[str]) -> dict[str, int]:
    """Fetch OI for each symbol. Skips illiquid / failed symbols."""
    oi_map: dict[str, int] = {}
    for sym in symbols:
        try:
            resp = requests.post(
                f"{OPENALGO_BASE}/api/v1/quotes",
                json={"apikey": api_key, "symbol": sym, "exchange": "NFO"},
                headers=_headers(api_key),
                timeout=8,
            )
            if resp.status_code != 200:
                continue
            data = resp.json()
            oi = data.get("oi") or data.get("data", {}).get("oi", 0)
            oi_map[sym] = int(oi or 0)
        except Exception:
            pass
    return oi_map


# ---------------------------------------------------------------------------
# OI calculations
# ---------------------------------------------------------------------------

def _compute_pcr(oi_map: dict[str, int]) -> float:
    pe_oi = sum(v for k, v in oi_map.items() if k.endswith("PE"))
    ce_oi = sum(v for k, v in oi_map.items() if k.endswith("CE"))
    return round(pe_oi / ce_oi, 3) if ce_oi > 0 else 0.0


def _compute_max_pain(
    oi_map: dict[str, int], strikes: list[int], underlying: str, exp_code: str
) -> int:
    """
    Max pain = strike minimising total intrinsic value payout to option buyers.
    Uses the actual strikes list to avoid re-parsing symbol strings.
    """
    min_payout = float("inf")
    max_pain_strike = strikes[len(strikes) // 2]

    for candidate in strikes:
        payout = sum(
            max(0, candidate - s) * oi_map.get(f"{underlying}{exp_code}{s}CE", 0)
            + max(0, s - candidate) * oi_map.get(f"{underlying}{exp_code}{s}PE", 0)
            for s in strikes
        )
        if payout < min_payout:
            min_payout = payout
            max_pain_strike = candidate

    return max_pain_strike


def _find_walls(
    oi_map: dict[str, int], spot: float, strikes: list[int],
    underlying: str, exp_code: str
) -> tuple[int, int, int, int]:
    """Returns (ce_wall_strike, ce_wall_oi, pe_wall_strike, pe_wall_oi).
    Uses actual strikes list to avoid regex year+strike ambiguity."""
    step = STRIKE_STEP.get(underlying, 100)
    atm = round(spot / step) * step

    ce_map = {s: oi_map.get(f"{underlying}{exp_code}{s}CE", 0) for s in strikes if s >= atm}
    pe_map = {s: oi_map.get(f"{underlying}{exp_code}{s}PE", 0) for s in strikes if s <= atm}

    if not ce_map or not pe_map:
        return atm + 5 * step, 0, atm - 5 * step, 0

    ce_wall = max(ce_map, key=ce_map.get)
    pe_wall = max(pe_map, key=pe_map.get)
    return ce_wall, ce_map[ce_wall], pe_wall, pe_map[pe_wall]


# ---------------------------------------------------------------------------
# Strategy recommendation
# ---------------------------------------------------------------------------

def _recommend_strategy(
    pcr: float,
    pcr_trend: str,
    spot: float,
    ce_wall: int,
    pe_wall: int,
    max_pain: int,
    vix: Optional[float],
    ta_signal: Optional[str],
    ta_confidence: Optional[float],
) -> tuple[str, str]:
    """
    Returns (strategy, reason).

    Rules (in priority order):
    1. VIX > 22 → hold (too risky to sell premium)
    2. Strong directional signal (confidence > 0.70):
       BUY → bull_put_spread
       SELL → bear_call_spread
    3. Neutral / moderate (PCR 0.80–1.30, range not extreme):
       Tight range (< 2% of spot) → short_straddle
       Moderate range → iron_condor
    4. PCR extreme (> 1.4 = heavy put writing, very bullish → too complacent → hold)
       PCR < 0.6 = heavy call writing, very bearish → too fearful → hold
    5. Default → iron_condor
    """
    range_pts = ce_wall - pe_wall
    range_pct = range_pts / spot * 100 if spot > 0 else 5.0

    # Rule 1: Very high VIX
    if vix and vix > 22:
        return "hold", f"VIX {vix:.1f} > 22 — premium selling too risky in high vol environment"

    # Rule 2: Strong directional conviction
    if ta_signal and ta_confidence and ta_confidence >= 0.70:
        if ta_signal == "BUY":
            return "bull_put_spread", (
                f"TradingAgents BUY signal conf={ta_confidence:.2f}. "
                f"Sell PE spread below spot. PCR {pcr:.2f} ({pcr_trend})."
            )
        if ta_signal == "SELL":
            return "bear_call_spread", (
                f"TradingAgents SELL signal conf={ta_confidence:.2f}. "
                f"Sell CE spread above spot. PCR {pcr:.2f} ({pcr_trend})."
            )

    # Rule 3 & 5: Neutral signal or moderate confidence
    if 0.70 <= pcr <= 1.35:
        if range_pct < 1.8:
            return "short_straddle", (
                f"Tight expected range {range_pts:.0f}pts ({range_pct:.1f}%). "
                f"PCR {pcr:.2f} — neutral. Short straddle near max pain {max_pain}."
            )
        return "iron_condor", (
            f"PCR {pcr:.2f} ({pcr_trend}) — balanced. "
            f"Range {pe_wall}–{ce_wall} ({range_pts:.0f}pts). "
            f"Iron condor within OI walls."
        )

    # Rule 4: Extreme PCR
    if pcr > 1.4:
        return "hold", f"PCR {pcr:.2f} extremely elevated — over-bullish, wait for mean reversion"
    if pcr < 0.60:
        return "hold", f"PCR {pcr:.2f} extremely low — over-bearish, wait for clarity"

    return "iron_condor", f"Default: iron_condor. PCR {pcr:.2f}, range {pe_wall}–{ce_wall}."


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_oi_analysis(
    underlying: str,
    api_key: str,
    expiry: Optional[str] = None,
    expiry_offset: int = 0,
    ta_signal: Optional[str] = None,
    ta_confidence: Optional[float] = None,
    vix: Optional[float] = None,
    morning_pcr_snapshot: Optional[float] = None,
) -> OIAnalysis:
    """
    Full OI analysis for the given underlying.

    ta_signal / ta_confidence: from TradingAgents morning brief (optional)
    morning_pcr_snapshot: PCR recorded at 9:15am for trend comparison (optional)
    """
    spot = _get_spot(api_key, underlying)
    step = STRIKE_STEP.get(underlying, 100)
    atm = round(spot / step) * step

    if expiry is None:
        expiry, exp_code = _nearest_expiry_from_db(underlying, offset=expiry_offset)
    else:
        dt = datetime.strptime(expiry, "%Y-%m-%d")
        exp_code = dt.strftime("%d%b%y").upper()

    print(f"  OI Analyst: {underlying} spot={spot:.0f} ATM={atm} expiry={expiry} ({exp_code})", flush=True)

    symbols, strikes = _build_symbols(underlying, atm, exp_code)
    print(f"  Fetching OI for {len(symbols)} strikes...", flush=True)
    oi_map = _fetch_oi_map(api_key, symbols)
    print(f"  Got OI data for {len(oi_map)} symbols", flush=True)

    if not strikes:
        # Market closed or OI unavailable — return minimal result
        return OIAnalysis(
            underlying=underlying, expiry=expiry, spot=spot, atm_strike=atm,
            pcr=1.0, pcr_trend="flat", pcr_morning=morning_pcr_snapshot,
            max_pain=atm, ce_wall=atm + 5 * step, pe_wall=atm - 5 * step,
            ce_wall_oi=0, pe_wall_oi=0,
            expected_range_low=atm - 5 * step, expected_range_high=atm + 5 * step,
            expected_range_str=f"{atm - 5*step}–{atm + 5*step}",
            total_ce_oi=0, total_pe_oi=0,
            strategy_recommendation="hold",
            strategy_reason="No OI data available",
            summary="OI data unavailable — markets may be closed.",
        )

    total_ce_oi = sum(v for k, v in oi_map.items() if k.endswith("CE"))
    total_pe_oi = sum(v for k, v in oi_map.items() if k.endswith("PE"))
    pcr = _compute_pcr(oi_map)
    max_pain = _compute_max_pain(oi_map, strikes, underlying, exp_code)
    ce_wall, ce_wall_oi, pe_wall, pe_wall_oi = _find_walls(oi_map, spot, strikes, underlying, exp_code)

    # PCR trend
    if morning_pcr_snapshot:
        if pcr > morning_pcr_snapshot * 1.03:
            pcr_trend = "rising"
        elif pcr < morning_pcr_snapshot * 0.97:
            pcr_trend = "falling"
        else:
            pcr_trend = "flat"
    else:
        pcr_trend = "flat"

    range_low = pe_wall
    range_high = ce_wall

    strategy, strategy_reason = _recommend_strategy(
        pcr=pcr, pcr_trend=pcr_trend, spot=spot,
        ce_wall=ce_wall, pe_wall=pe_wall, max_pain=max_pain,
        vix=vix, ta_signal=ta_signal, ta_confidence=ta_confidence,
    )

    summary = (
        f"Max pain {max_pain}. PCR {pcr:.2f} ({pcr_trend}). "
        f"CE wall (resistance) at {ce_wall} ({ce_wall_oi:,} OI). "
        f"PE wall (support) at {pe_wall} ({pe_wall_oi:,} OI). "
        f"Expected range: {range_low}–{range_high}. "
        f"Recommended: {strategy}."
    )

    return OIAnalysis(
        underlying=underlying, expiry=expiry, spot=spot, atm_strike=atm,
        pcr=pcr, pcr_trend=pcr_trend, pcr_morning=morning_pcr_snapshot,
        max_pain=max_pain,
        ce_wall=ce_wall, ce_wall_oi=ce_wall_oi,
        pe_wall=pe_wall, pe_wall_oi=pe_wall_oi,
        expected_range_low=range_low, expected_range_high=range_high,
        expected_range_str=f"{range_low}–{range_high}",
        total_ce_oi=total_ce_oi, total_pe_oi=total_pe_oi,
        strategy_recommendation=strategy,
        strategy_reason=strategy_reason,
        summary=summary,
    )


# ---------------------------------------------------------------------------
# Standalone test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--underlying", default="BANKNIFTY", choices=["BANKNIFTY", "NIFTY"])
    parser.add_argument("--expiry", help="Expiry YYYY-MM-DD (default: nearest Thursday)")
    args = parser.parse_args()

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
        sys.exit("Set OPENALGO_API_KEY in ~/autotrade/.env")

    result = run_oi_analysis(
        underlying=args.underlying,
        api_key=api_key,
        expiry=args.expiry,
    )
    print(json.dumps(result.model_dump(), indent=2))
