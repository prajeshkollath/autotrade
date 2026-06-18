#!/usr/bin/env python3
"""
backtest_replay.py — Replay historical NIFTY options data through the live agent pipeline.

HOW TO RUN:
  cd /home/freed/autotrade
  source .env
  .venv/bin/python3.12 agents/backtest_replay.py --date 2025-06-03 --speed 30

  --date   : historical trading date to replay (YYYY-MM-DD)
  --speed  : seconds to wait between 5-min bars (default 60; use 10 for fast test)
  --lots   : number of lots per leg (default 5)
  --otm    : OTM offset in strikes from ATM (default 3 = 150 pts for NIFTY 50-pt steps)

Each run creates a unique replay_id (e.g. bt_nifty_20250603_0942) so multiple
replays don't overwrite each other.

Watch live at:
  https://trading.34-45-46-60.sslip.io/?underlying=NIFTY&strategy_id=<replay_id>
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR   = Path(__file__).parent.parent
AGENTS_DIR = Path(__file__).parent
CATALOG_DIR = BASE_DIR / "data" / "catalog"
LOG_DIR     = BASE_DIR / "data" / "decision_logs"
SM_DIR      = BASE_DIR / "data" / "session_memory"

sys.path.insert(0, str(AGENTS_DIR))

IST = timezone(timedelta(hours=5, minutes=30))
LOT_SIZE   = 75   # NIFTY lot size
STRIKE_STEP = 50  # NIFTY strike step in points


# ── Load env ──────────────────────────────────────────────────────────────────
def _load_env():
    env = BASE_DIR / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())

_load_env()


# ── Catalog helpers ────────────────────────────────────────────────────────────
def _load_catalog():
    from nautilus_trader.persistence.catalog import ParquetDataCatalog
    return ParquetDataCatalog(str(CATALOG_DIR))


def _nearest_expiry(catalog, replay_date, min_dte: int = 7) -> Optional[str]:
    # Pick nearest expiry with DTE >= min_dte; fall back to nearest if none
    all_insts = catalog.instruments()
    expiries = sorted(set(str(i.id).split("_")[1] for i in all_insts
                          if str(i.id).startswith("NIFTY_")))
    first_valid = None
    for exp in expiries:
        exp_dt = datetime.strptime(exp, "%Y%m%d").date()
        if first_valid is None and exp_dt >= replay_date:
            first_valid = exp
        if exp_dt >= replay_date and (exp_dt - replay_date).days >= min_dte:
            return exp
    return first_valid



def _load_day_bars(catalog, expiry: str, replay_date, inst_ids: list[str]) -> dict:
    """Load bars for given instruments on replay_date. Returns {inst_id: [bar,...]}"""
    result = {}
    for inst_id in inst_ids:
        try:
            bars = catalog.bars([inst_id])
            day = [b for b in bars
                   if datetime.fromtimestamp(b.ts_event / 1e9, tz=timezone.utc)
                      .astimezone(IST).date() == replay_date]
            if day:
                seen_ts = set()
                deduped = []
                for b in sorted(day, key=lambda b: b.ts_event):
                    if b.ts_event not in seen_ts:
                        seen_ts.add(b.ts_event)
                        deduped.append(b)
                result[inst_id] = deduped
        except Exception:
            pass
    return result


def _bar_at(bars: list, ts: datetime) -> Optional[object]:
    """Find bar closest to (but not after) the given IST timestamp."""
    target_min = ts.hour * 60 + ts.minute
    best = None
    for b in bars:
        b_ist = datetime.fromtimestamp(b.ts_event / 1e9, tz=timezone.utc).astimezone(IST)
        b_min = b_ist.hour * 60 + b_ist.minute
        if b_min <= target_min:
            best = b
        else:
            break
    return best


def _get_spot(proxy_strike: int, proxy_ce_bars: list, proxy_pe_bars: list,
              bar_ts: datetime) -> Optional[float]:
    """Estimate NIFTY spot using put-call parity on the ATM proxy pair."""
    ce = _bar_at(proxy_ce_bars, bar_ts)
    pe = _bar_at(proxy_pe_bars, bar_ts)
    if ce is None or pe is None:
        return None
    return round(proxy_strike + float(ce.close) - float(pe.close), 2)


def _find_atm(catalog, expiry: str, replay_date, all_insts) -> tuple[int, float]:
    """
    At 09:20 on replay_date, find ATM strike (CE ≈ PE) and return
    (atm_strike, spot_estimate).

    Fast 2-pass approach: coarse scan with stride to estimate spot,
    then fine scan of ±5 strikes around estimate.  Avoids loading
    all 300+ parquet files when a large expiry is used.
    """
    exp_insts = [i for i in all_insts if f"_{expiry}_" in str(i.id)]
    strikes = sorted(set(int(str(i.id).split("_")[2]) for i in exp_insts))

    open_ts = datetime.combine(replay_date,
                               datetime.min.time().replace(hour=9, minute=20)
                               ).replace(tzinfo=IST)

    # ── Pass 1: coarse — sample every 10th strike to locate approximate spot ──
    stride = max(1, len(strikes) // 20)   # ~20 reads regardless of expiry size
    coarse_spot = None
    for i in range(len(strikes) // 2, len(strikes), stride):  # start from middle
        s = strikes[i]
        ce_id = f"NIFTY_{expiry}_{s}_CE.NSE"
        pe_id = f"NIFTY_{expiry}_{s}_PE.NSE"
        db = _load_day_bars(catalog, expiry, replay_date, [ce_id, pe_id])
        cb = _bar_at(db.get(ce_id, []), open_ts)
        pb = _bar_at(db.get(pe_id, []), open_ts)
        if cb and pb:
            coarse_spot = round(s + float(cb.close) - float(pb.close), 2)
            break
    # Also scan from middle downward if not found yet
    if coarse_spot is None:
        for i in range(len(strikes) // 2, -1, -stride):
            s = strikes[i]
            ce_id = f"NIFTY_{expiry}_{s}_CE.NSE"
            pe_id = f"NIFTY_{expiry}_{s}_PE.NSE"
            db = _load_day_bars(catalog, expiry, replay_date, [ce_id, pe_id])
            cb = _bar_at(db.get(ce_id, []), open_ts)
            pb = _bar_at(db.get(pe_id, []), open_ts)
            if cb and pb:
                coarse_spot = round(s + float(cb.close) - float(pb.close), 2)
                break

    if coarse_spot is None:
        return None, None   # no data for this date

    # ── Pass 2: fine — scan ±5 strikes around estimated spot ─────────────────
    near = [s for s in strikes if abs(s - coarse_spot) <= 300]   # 300pt window
    best_strike, best_spot, best_diff = None, None, float("inf")
    for strike in near:
        ce_id = f"NIFTY_{expiry}_{strike}_CE.NSE"
        pe_id = f"NIFTY_{expiry}_{strike}_PE.NSE"
        day_bars = _load_day_bars(catalog, expiry, replay_date, [ce_id, pe_id])
        ce_bar = _bar_at(day_bars.get(ce_id, []), open_ts)
        pe_bar = _bar_at(day_bars.get(pe_id, []), open_ts)
        if not ce_bar or not pe_bar:
            continue
        ce_ltp = float(ce_bar.close)
        pe_ltp = float(pe_bar.close)
        diff = abs(ce_ltp - pe_ltp)
        if diff < best_diff:
            best_diff = diff
            best_strike = strike
            best_spot = round(strike + ce_ltp - pe_ltp, 2)

    return best_strike, best_spot


# ── ContextSnapshot builder ────────────────────────────────────────────────────
def _build_context(
    bar_ts: datetime,
    spot: float,
    entry_spot: float,
    expiry_str: str,
    positions: list[dict],      # [{symbol, qty, avg_price, strike, opt_type, bars}]
    replay_date,
    intraday_high: float,
    intraday_low: float,
    bars_since_last_add: int = 999,
    realized_pnl: float = 0.0,  # cumulative P&L from already-closed legs
) -> object:
    """Build a ContextSnapshot from historical bar data."""
    from goal_schema import ContextSnapshot, PositionSnapshot

    expiry_dt = datetime.strptime(expiry_str, "%Y%m%d").replace(
        hour=15, minute=30, tzinfo=IST)
    tte_hours = max((expiry_dt - bar_ts).total_seconds() / 3600, 0)
    dte = max((expiry_dt.date() - replay_date).days, 0)

    pos_snapshots = []
    total_pnl = 0.0
    net_delta = 0.0
    net_theta = 0.0
    net_vega  = 0.0

    for p in positions:
        if p["qty"] == 0:
            continue
        bar = _bar_at(p["bars"], bar_ts)
        if bar is None:
            continue
        ltp = float(bar.close)
        avg_price = p["avg_price"]
        qty = p["qty"]
        pnl = (avg_price - ltp) * abs(qty)   # short position: profit when ltp drops
        total_pnl += pnl

        strike = p["strike"]
        opt_type = p["opt_type"]
        if opt_type == "CE":
            otm_pct = max((strike - spot) / spot, 0.0)
        else:
            otm_pct = max((spot - strike) / spot, 0.0)

        premium_ratio = round(ltp / avg_price, 3) if avg_price > 0 else None

        # Approximate Greeks via Black-Scholes
        delta = theta = vega = None
        try:
            from opengreeks.black_scholes import black_scholes as _bs
            flag = "c" if opt_type == "CE" else "p"
            iv = 0.15  # rough IV estimate; good enough for context
            result = _bs(flag, spot, strike, tte_hours / (365 * 24),
                         0.065, iv)
            delta = round(result.get("delta", 0) * qty, 4)
            theta = round(result.get("theta", 0) * abs(qty), 4)
            vega  = round(result.get("vega",  0) * abs(qty), 4)
            net_delta += delta or 0
            net_theta += theta or 0
            net_vega  += vega or 0
        except Exception:
            pass

        pos_snapshots.append(PositionSnapshot(
            symbol=p["symbol"],
            product="MIS",
            qty=qty,
            avg_price=round(avg_price, 2),
            ltp=round(ltp, 2),
            pnl=round(pnl, 2),
            delta=delta,
            theta=theta,
            vega=vega,
            otm_pct=round(otm_pct, 4),
            premium_ratio=premium_ratio,
            dte=dte,
        ))

    return ContextSnapshot(
        timestamp_ist=bar_ts.strftime("%H:%M IST"),
        current_pnl=round(total_pnl + realized_pnl, 2),  # open + closed legs
        net_delta=round(net_delta, 4),
        net_theta=round(net_theta, 4),
        net_vega=round(net_vega, 4),
        underlying_price=spot,
        underlying_move_pts=round(spot - entry_spot, 2),
        underlying_move_pct=round((spot - entry_spot) / entry_spot * 100, 2),
        vix_now=None,
        pcr_now=None,
        pcr_trend=None,
        time_to_expiry_hours=round(tte_hours, 2),
        positions=pos_snapshots,
        oi_shift_summary="[replay] no live OI data",
        intraday_high=intraday_high,
        intraday_low=intraday_low,
        bar_dt=bar_ts,
        bars_since_last_add=bars_since_last_add,
    )


# ── Decision logging ───────────────────────────────────────────────────────────
def _log_decision(goal_dict, ctx, decision, executed, exec_detail, source, strategy_id, bar_ts=None):
    """Append to today's NIFTY decision log (filtered by strategy_id in dashboard)."""
    import json as _j
    underlying = goal_dict.get("underlying", "NIFTY").upper()
    today = datetime.now(IST).strftime("%Y-%m-%d")
    log_path = LOG_DIR / f"{today}-{underlying}.jsonl"
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    record = {
        "ts":               (bar_ts if bar_ts else datetime.now(IST)).isoformat(),
        "goal":             goal_dict,
        "context_summary":  {
            "timestamp":         ctx.timestamp_ist,
            "pnl":               ctx.current_pnl,
            "underlying_price":  ctx.underlying_price,
            "underlying_move_pts": ctx.underlying_move_pts,
            "vix":               ctx.vix_now,
            "pcr":               ctx.pcr_now,
            "pcr_trend":         ctx.pcr_trend,
            "tte_hours":         ctx.time_to_expiry_hours,
        },
        "decision":         decision.model_dump() if hasattr(decision, "model_dump") else decision,
        "decision_source":  source,
        "executed":         executed,
        "execution_detail": exec_detail,
    }
    with open(log_path, "a") as f:
        f.write(_j.dumps(record) + "\n")


# ── Main replay loop ───────────────────────────────────────────────────────────
def run_replay(replay_date_str: str, speed_secs: int, lots: int, otm_steps: int, use_llm: bool = False, screen_name: str = ""):
    from goal_schema import Goal, Decision
    from position_manager import behavioral_checks, call_llm, _send_telegram, get_llm_usage
    import session_memory as _sm
    from openai import OpenAI
    import httpx

    replay_date = datetime.strptime(replay_date_str, "%Y-%m-%d").date()
    # Derive run_ts from screen_name if provided (dashboard passes replay_nifty_YYYYMMDD_HHMMss)
    # so strategy_id stays consistent with screen name for the Stop button
    if screen_name:
        run_ts = screen_name.rsplit("_", 1)[-1]
    else:
        run_ts = datetime.now(IST).strftime("%H%M%S")
    replay_id = f"bt_nifty_{replay_date_str.replace('-','')}_{run_ts}"
    underlying = "NIFTY"
    # Write a "loading" stub immediately so the dashboard shows the session right away
    # (catalog loading takes 1-3 min; without this, the UI shows nothing)
    from pathlib import Path as _Path
    _stub_dir = _Path("/home/freed/autotrade/data/session_memory")
    _stub_dir.mkdir(parents=True, exist_ok=True)
    _today = datetime.now(IST).strftime("%Y-%m-%d")
    _stub_path = _stub_dir / f"{_today}-{underlying.upper()}-{replay_id}.json"
    import json as _json_stub
    _stub_path.write_text(_json_stub.dumps({
        "header": {
            "status": "loading",
            "date": replay_date_str,
            "underlying": underlying,
            "strategy_id": replay_id,
            "screen_name": screen_name,
        }
    }, indent=2))



    print(f"\n{'='*60}")
    print(f"BACKTEST REPLAY — NIFTY Short Strangle")
    print(f"  Historical date : {replay_date_str}")
    print(f"  Replay ID       : {replay_id}")
    print(f"  Speed           : {speed_secs}s per 5-min bar")
    print(f"  Lots            : {lots} per leg")
    print(f"  OTM steps       : {otm_steps} x 50pts = {otm_steps*50}pts OTM")
    print(f"{'='*60}\n")

    print("Loading catalog...")
    catalog = _load_catalog()
    all_insts = catalog.instruments()

    # Find nearest expiry
    expiry = _nearest_expiry(catalog, replay_date)
    if not expiry:
        print("ERROR: No expiry found for this date")
        sys.exit(1)
    expiry_date = datetime.strptime(expiry, "%Y%m%d").date()
    dte_at_open = (expiry_date - replay_date).days
    print(f"Expiry: {expiry} (DTE={dte_at_open})")

    # Find ATM at 09:20
    print("Finding ATM strike at 09:20...")
    atm_strike, open_spot = _find_atm(catalog, expiry, replay_date, all_insts)
    if not atm_strike:
        print("ERROR: Could not determine ATM strike — no data for this date")
        sys.exit(1)
    print(f"ATM strike: {atm_strike}  |  Estimated spot: {open_spot}")

    ce_strike = atm_strike + otm_steps * STRIKE_STEP
    pe_strike = atm_strike - otm_steps * STRIKE_STEP
    print(f"Strangle: CE={ce_strike}  PE={pe_strike}")

    # Instrument IDs
    ce_sym  = f"NIFTY{expiry[2:4]}{['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC'][int(expiry[4:6])-1]}{expiry[6:8]}{ce_strike}CE"
    pe_sym  = f"NIFTY{expiry[2:4]}{['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC'][int(expiry[4:6])-1]}{expiry[6:8]}{pe_strike}PE"
    ce_id   = f"NIFTY_{expiry}_{ce_strike}_CE.NSE"
    pe_id   = f"NIFTY_{expiry}_{pe_strike}_PE.NSE"
    proxy_ce_id = f"NIFTY_{expiry}_{atm_strike}_CE.NSE"
    proxy_pe_id = f"NIFTY_{expiry}_{atm_strike}_PE.NSE"

    print("Loading bar data...")
    bar_data = _load_day_bars(catalog, expiry, replay_date,
                               [ce_id, pe_id, proxy_ce_id, proxy_pe_id])

    if ce_id not in bar_data or pe_id not in bar_data:
        print(f"ERROR: No bars found for CE={ce_id} or PE={pe_id}")
        sys.exit(1)

    proxy_ce_bars = bar_data.get(proxy_ce_id, [])
    proxy_pe_bars = bar_data.get(proxy_pe_id, [])

    # Get all bar timestamps for the day (use CE bars as timeline)
    all_bars = bar_data[ce_id]
    n_bars = len(all_bars)
    print(f"Bars found: {n_bars} ({all_bars[0] and datetime.fromtimestamp(all_bars[0].ts_event/1e9, tz=timezone.utc).astimezone(IST).strftime('%H:%M')} → {datetime.fromtimestamp(all_bars[-1].ts_event/1e9, tz=timezone.utc).astimezone(IST).strftime('%H:%M')})\n")

    # Entry prices at first bar
    first_bar_ts = datetime.fromtimestamp(all_bars[0].ts_event / 1e9, tz=timezone.utc).astimezone(IST)
    ce_entry = float(_bar_at(bar_data[ce_id], first_bar_ts).close)
    pe_entry = float(_bar_at(bar_data[pe_id], first_bar_ts).close)
    qty_per_leg = -lots * LOT_SIZE   # negative = short

    positions = [
        {"symbol": ce_sym, "qty": qty_per_leg, "avg_price": ce_entry,
         "strike": ce_strike, "opt_type": "CE", "bars": bar_data[ce_id]},
        {"symbol": pe_sym, "qty": qty_per_leg, "avg_price": pe_entry,
         "strike": pe_strike, "opt_type": "PE", "bars": bar_data[pe_id]},
    ]

    print(f"Entry:")
    print(f"  SELL {abs(qty_per_leg)} {ce_sym} @ {ce_entry}")
    print(f"  SELL {abs(qty_per_leg)} {pe_sym} @ {pe_entry}\n")

    # Build goal
    target_pnl  = round((ce_entry + pe_entry) * abs(qty_per_leg) * 0.5)  # 50% of premium
    max_loss_pnl = -round((ce_entry + pe_entry) * abs(qty_per_leg) * 1.5)
    goal = Goal(
        strategy_id=replay_id,
        strategy_type="options",
        strategy="short_strangle",
        underlying=underlying,
        target_profit=float(target_pnl),
        max_loss=float(max_loss_pnl),
        delta_tolerance=0.25,
        protect_at_pct=0.5,
        expiry=expiry_date.strftime("%Y-%m-%d"),
        style="conservative",
        lots=lots,
        entry_price=open_spot,
    )

    goal_dict = goal.model_dump()
    goal_dict["strategy_id"] = replay_id   # ensure replay_id used everywhere

    # Init session memory
    _sm.init_session(
        underlying=underlying,
        entry_spot=open_spot,
        goal=goal_dict,
        positions=[{"symbol": p["symbol"], "qty": p["qty"], "avg_price": p["avg_price"]}
                   for p in positions],
        strategy_id=replay_id,
    )
    # Update stub status from "loading" -> "active" now that we have real entry data
    _d = _sm._load(underlying, replay_id)
    _d.setdefault("header", {})["status"] = "active"
    _d["header"]["started_at"] = datetime.now(IST).strftime("%H:%M IST")
    _d["header"]["expiry"] = expiry
    _d["header"]["date"] = replay_date_str
    _d["header"].setdefault("entry_spot", round(open_spot))
    _sm._save(underlying, replay_id, _d)
    for p in positions:
        _sm.add_owned_symbol(underlying, p["symbol"], replay_id)

    # OpenAI client
    client = OpenAI(
        api_key=os.environ["OPENAI_API_KEY"],
        timeout=httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=5.0),
    )

    intraday_high = open_spot
    intraday_low  = open_spot
    session_closed = False

    print(f"Dashboard URL:")
    print(f"  https://trading.34-45-46-60.sslip.io/?underlying=NIFTY&strategy_id={replay_id}\n")
    print(f"Starting replay at {speed_secs}s per bar...\n")

    fills_log = []
    for p in positions:
        if p["qty"] != 0:
            fills_log.append({
                "time":   first_bar_ts.strftime("%H:%M"),
                "symbol": p["symbol"],
                "action": "SELL",
                "qty":    abs(p["qty"]),
                "price":  p["avg_price"],
            })

    last_add_bar_idx   = -999
    realized_pnl       = 0.0    # cumulative P&L from legs that have been closed
    prev_spot          = None   # spot from previous bar (center-crossing detection)
    prev_in_loss       = False  # was P&L < 0 last bar
    last_loss_add_type = None   # "CE" or "PE" — last add in loss mode
    loss_otm_steps      = otm_steps - 1  # 1 step tighter than entry; decrements each add
    peak_pnl            = 0.0             # trailing: highest P&L seen this replay
    rebalance_otm_steps = otm_steps - 1   # OTM steps for REBALANCE/TRAIL adds
    bars_beyond_be      = 0               # consecutive bars spot outside a BE
    rebalance_safe_done = False            # True after REBALANCE_SAFE fires; reset when balanced
    ce_exit_spot       = None              # spot when CE last closed via PARTIAL_EXIT
    pe_exit_spot       = None              # spot when PE last closed via PARTIAL_EXIT
    for bar_idx, bar in enumerate(all_bars):
        if session_closed:
            break

        bar_ts = datetime.fromtimestamp(bar.ts_event / 1e9, tz=timezone.utc).astimezone(IST)

        # Derive spot
        if proxy_ce_bars and proxy_pe_bars:
            spot = _get_spot(atm_strike, proxy_ce_bars, proxy_pe_bars, bar_ts)
        else:
            spot = None
        if spot is None:
            spot = open_spot   # fallback

        intraday_high = max(intraday_high, spot)
        intraday_low  = min(intraday_low, spot)

        print(f"\n{'─'*54}")
        print(f"Bar {bar_idx+1}/{n_bars} — {bar_ts.strftime('%H:%M IST')}  "
              f"[{replay_date_str}]  Spot: {spot:.0f}")

        # Build context
        ctx = _build_context(
            bar_ts=bar_ts,
            spot=spot,
            entry_spot=open_spot,
            realized_pnl=realized_pnl,
            expiry_str=expiry,
            positions=[p for p in positions if p["qty"] != 0],
            replay_date=replay_date,
            intraday_high=intraday_high,
            intraday_low=intraday_low,
            bars_since_last_add=bar_idx - last_add_bar_idx,
        )

        if not ctx.positions:
            print("  No active positions — session ended")
            break

        # Print position summary
        for ps in ctx.positions:
            flag = ""
            if ps.otm_pct is not None:
                if ps.otm_pct < 0.005:  flag = " !! CRITICAL"
                elif ps.otm_pct < 0.01: flag = " ! DANGER"
                elif ps.otm_pct < 0.02: flag = " ~ WARNING"
            if ps.premium_ratio is not None and ps.premium_ratio > 2.0:
                flag += " DOUBLED"
            print(f"    {ps.symbol:35s}  OTM={ps.otm_pct*100:.2f}%  "
                  f"ratio={ps.premium_ratio:.2f}x  P&L=Rs.{ps.pnl:+,.0f}{flag}")

        print(f"  Net P&L: Rs.{ctx.current_pnl:+,.0f}  |  "
              f"Spot move: {ctx.underlying_move_pts:+.0f}pts")

        # Step 1: Behavioural checks
        decision = behavioral_checks(
            ctx, goal,
            prev_spot=prev_spot,
            last_loss_add_type=last_loss_add_type,
            loss_otm_steps=loss_otm_steps,
            prev_in_loss=prev_in_loss,
            peak_pnl=peak_pnl,
            rebalance_otm_steps=rebalance_otm_steps,
            bars_beyond_be=bars_beyond_be,
            rebalance_safe_done=rebalance_safe_done,
            ce_exit_spot=ce_exit_spot,
            pe_exit_spot=pe_exit_spot,
        )
        source = "rules"

        if decision and decision.action == "FULL_EXIT":
            session_closed = True

        # Step 2: LLM if no rule triggered (skipped when use_llm=False)
        if decision is None:
            if use_llm:
                print("  Calling LLM...", end="", flush=True)
                decision = call_llm(goal, ctx, client)
                source = "llm"
            else:
                from goal_schema import Decision as _D
                decision = _D(action="HOLD", reasoning="[rules-only mode] No rule triggered.",
                              urgency="low", next_review="5min")
                source = "rules"

        print(f" [{source}] → {decision.action}")
        print(f"  Reasoning: {decision.reasoning[:120]}")

        # Step 3: Simulate execution (paper only — no real orders)
        executed = False
        exec_detail = "replay — paper only"

        if decision.action == "ADD_POSITION":
            # Load bars for the new leg
            opt_type = (decision.instrument or "CE").upper()
            # Respect target_otm_pct if set (BE_RECENTER uses 1.0-1.5% OTM)
            _tgt_otm = getattr(decision, "target_otm_pct", None)
            if _tgt_otm and _tgt_otm > 0:
                _otm_pts = int(spot * _tgt_otm)
                if opt_type == "CE":
                    new_strike = (int(spot / STRIKE_STEP) + 1) * STRIKE_STEP + int(_otm_pts / STRIKE_STEP) * STRIKE_STEP
                else:
                    new_strike = int((spot - _otm_pts) / STRIKE_STEP) * STRIKE_STEP
            else:
                if opt_type == "CE":
                    new_strike = atm_strike + otm_steps * STRIKE_STEP
                else:
                    new_strike = atm_strike - otm_steps * STRIKE_STEP
            new_id  = f"NIFTY_{expiry}_{new_strike}_{opt_type}.NSE"
            new_sym = f"NIFTY{expiry[2:4]}{['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC'][int(expiry[4:6])-1]}{expiry[6:8]}{new_strike}{opt_type}"
            add_bars = _load_day_bars(catalog, expiry, replay_date, [new_id])
            if new_id in add_bars:
                add_bar = _bar_at(add_bars[new_id], bar_ts)
                if add_bar:
                    add_entry = float(add_bar.close)
                    new_pos = {"symbol": new_sym, "qty": qty_per_leg,
                               "avg_price": add_entry, "strike": new_strike,
                               "opt_type": opt_type, "bars": add_bars[new_id]}
                    positions.append(new_pos)
                    _sm.add_owned_symbol(underlying, new_sym, replay_id)
                    fills_log.append({
                        "time":   bar_ts.strftime("%H:%M"),
                        "symbol": new_sym,
                        "action": "SELL",
                        "qty":    abs(qty_per_leg),
                        "price":  add_entry,
                    })
                    exec_detail = f"[REPLAY] ADD {new_sym} SELL {abs(qty_per_leg)} @ {add_entry}"
                    last_add_bar_idx = bar_idx  # cooldown starts
                    # Track which side was added in loss mode and tighten OTM
                    if ctx.current_pnl < 0 and hasattr(decision, "instrument") and decision.instrument:
                        last_loss_add_type = decision.instrument
                        loss_otm_steps = max(1, loss_otm_steps - 1)
                    # Tighten rebalance/trail OTM on each protective add
                    _rsn_br = getattr(decision, 'reasoning', '') or ''
                    if 'TRAIL_PROTECT' in _rsn_br or 'BE_RECENTER' in _rsn_br:
                        rebalance_otm_steps = max(1, rebalance_otm_steps - 1)
                    if 'TRAIL_PROTECT' in _rsn_br:
                        # Reset peak so TRAIL_PROTECT doesn't re-fire against old high
                        # when P&L recovers after a loss episode
                        peak_pnl = max(ctx.current_pnl, 0.0)
                        print(f'  [TRAIL] Peak reset to {peak_pnl:.0f} after add')
                    if "SUSTAINED_BE_BREACH" in (decision.reasoning or ""):
                        bars_beyond_be = 0
                    _rsn_rb2 = decision.reasoning or ""
                    if "REBALANCE_SAFE" in _rsn_rb2:
                        rebalance_safe_done = True
                    elif "REBALANCE_RESTORE" in _rsn_rb2:
                        rebalance_safe_done = False
                    _added_inst = (decision.instrument or "").upper()
                    if "CE" in _added_inst:
                        ce_exit_spot = None
                    elif "PE" in _added_inst:
                        pe_exit_spot = None
                    executed = True
                    print(f"  {exec_detail}")

        elif decision.action == "PARTIAL_EXIT":
            inst = decision.instrument or ""
            for p in positions:
                if p["symbol"] == inst and p["qty"] != 0:
                    exec_detail = f"[REPLAY] PARTIAL_EXIT {inst} closed"
                    exit_bar = _bar_at(p["bars"], bar_ts)
                    exit_ltp = float(exit_bar.close) if exit_bar else p["avg_price"]
                    realized_pnl += (p["avg_price"] - exit_ltp) * abs(p["qty"])
                    fills_log.append({
                        "time":     bar_ts.strftime("%H:%M"),
                        "symbol":   inst,
                        "action":   "BUY",
                        "qty":      abs(p["qty"]),
                        "price":    exit_ltp,
                        "avg_price": p["avg_price"],  # entry price for P&L computation
                    })
                    p["qty"] = 0
                    executed = True
                    if "CE" in inst.upper():
                        ce_exit_spot = spot
                    elif "PE" in inst.upper():
                        pe_exit_spot = spot
                    print(f"  {exec_detail}")
                    # After per-leg loss exit: reset peak (TRAIL_PROTECT) and cooldown
                    # (BE_RECENTER) so neither fires against the old position structure
                    last_add_bar_idx = bar_idx
                    # reset peak so TRAIL_PROTECT can't
                    # fire against a peak built with the now-closed position
                    peak_pnl = 0.0
                    # Same-bar second pass: rebuild ctx and re-run rules for ADD_POSITION
                    ctx2 = _build_context(
                        bar_ts=bar_ts, spot=spot, entry_spot=open_spot, realized_pnl=realized_pnl,
                        expiry_str=expiry, positions=[p for p in positions if p["qty"] != 0],
                        replay_date=replay_date, intraday_high=intraday_high, intraday_low=intraday_low,
                        bars_since_last_add=bar_idx - last_add_bar_idx,
                    )
                    _d2_added = False
                    d2 = behavioral_checks(
                        ctx2, goal, prev_spot=prev_spot, last_loss_add_type=last_loss_add_type,
                        loss_otm_steps=loss_otm_steps, prev_in_loss=prev_in_loss,
                        peak_pnl=0.0, rebalance_otm_steps=rebalance_otm_steps,  # no TRAIL_PROTECT after per-leg exit
                        bars_beyond_be=bars_beyond_be, rebalance_safe_done=rebalance_safe_done,
                        ce_exit_spot=ce_exit_spot, pe_exit_spot=pe_exit_spot,
                    )
                    if d2 and d2.action == "ADD_POSITION":
                        _rsn2_check = (d2.reasoning or "").upper()
                        if ("TRAIL_PROTECT" in _rsn2_check or "BE_RECENTER" in _rsn2_check
                                or "REBALANCE_SAFE" in _rsn2_check):
                            print(f"  [2ND-PASS] Skipping profit-mode/safe add after PARTIAL_EXIT: {(d2.reasoning or "")[:60]}")
                            d2 = None
                    if d2 and d2.action == "ADD_POSITION":
                        _opt2  = (d2.instrument or "CE").upper()
                        _tgt2  = getattr(d2, "target_otm_pct", None)
                        if _tgt2 and _tgt2 > 0:
                            _pts2 = int(spot * _tgt2)
                            if _opt2 == "CE":
                                _s2 = (int(spot / STRIKE_STEP) + 1) * STRIKE_STEP + int(_pts2 / STRIKE_STEP) * STRIKE_STEP
                            else:
                                _s2 = int((spot - _pts2) / STRIKE_STEP) * STRIKE_STEP
                        else:
                            _s2 = atm_strike + otm_steps * STRIKE_STEP if _opt2 == "CE" else atm_strike - otm_steps * STRIKE_STEP
                        _MONTHS2 = ["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"]
                        _id2  = f"NIFTY_{expiry}_{_s2}_{_opt2}.NSE"
                        _sym2 = f"NIFTY{expiry[2:4]}{_MONTHS2[int(expiry[4:6])-1]}{expiry[6:8]}{_s2}{_opt2}"
                        _b2s  = _load_day_bars(catalog, expiry, replay_date, [_id2])
                        if _id2 in _b2s:
                            _b2 = _bar_at(_b2s[_id2], bar_ts)
                            if _b2:
                                _e2 = float(_b2.close)
                                _d2_added = True
                                positions.append({"symbol": _sym2, "qty": qty_per_leg, "avg_price": _e2,
                                                   "strike": _s2, "opt_type": _opt2, "bars": _b2s[_id2]})
                                _sm.add_owned_symbol(underlying, _sym2, replay_id)
                                fills_log.append({"time": bar_ts.strftime("%H:%M"), "symbol": _sym2,
                                                   "action": "SELL", "qty": abs(qty_per_leg), "price": _e2})
                                _log_decision(goal_dict, ctx2, d2, True, f"[REPLAY] same-bar ADD {_sym2} @{_e2}", "rules", replay_id, bar_ts=bar_ts)
                                _rsn2 = d2.reasoning or ""
                                if "REBALANCE_SAFE" in _rsn2: rebalance_safe_done = True
                                elif "REBALANCE_RESTORE" in _rsn2: rebalance_safe_done = False
                                if "TRAIL_PROTECT" in _rsn2 or "BE_RECENTER" in _rsn2:
                                    rebalance_otm_steps = max(1, rebalance_otm_steps - 1)
                                if "TRAIL_PROTECT" in _rsn2:
                                    peak_pnl = max(ctx2.current_pnl, 0.0)
                                    print(f'  [TRAIL] Peak reset to {peak_pnl:.0f} after same-bar add')
                                if "LOSS_RECOVERY" in _rsn2:
                                    last_loss_add_type = d2.instrument
                                    loss_otm_steps = max(1, loss_otm_steps - 1)
                                _ai2 = (d2.instrument or "").upper()
                                if "CE" in _ai2: ce_exit_spot = None
                                elif "PE" in _ai2: pe_exit_spot = None
                    # Post-exit rebalance: add the OPPOSITE of the leg just closed.
                    # Only fires if the second-pass d2 did not already add a position.
                    # PE closed → add CE. CE closed → add PE.
                    _rebal_ex = None
                    if not _d2_added:
                        if "PE" in inst.upper() and rebalance_otm_steps >= 1:
                            _rebal_ex = "CE"
                        elif "CE" in inst.upper() and rebalance_otm_steps >= 1:
                            _rebal_ex = "PE"
                    if _rebal_ex:
                        _MONTHS_EX = ["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"]
                        _pts_ex = int(spot * rebalance_otm_steps * STRIKE_STEP / spot / 1) * STRIKE_STEP
                        _pts_ex = rebalance_otm_steps * STRIKE_STEP
                        if _rebal_ex == "CE":
                            _s_ex = (int(spot / STRIKE_STEP) + 1) * STRIKE_STEP + _pts_ex
                        else:
                            _s_ex = int((spot - _pts_ex) / STRIKE_STEP) * STRIKE_STEP
                        _id_ex  = f"NIFTY_{expiry}_{_s_ex}_{_rebal_ex}.NSE"
                        _sym_ex = f"NIFTY{expiry[2:4]}{_MONTHS_EX[int(expiry[4:6])-1]}{expiry[6:8]}{_s_ex}{_rebal_ex}"
                        _brs_ex = _load_day_bars(catalog, expiry, replay_date, [_id_ex])
                        if _id_ex in _brs_ex:
                            _bar_ex = _bar_at(_brs_ex[_id_ex], bar_ts)
                            if _bar_ex:
                                _e_ex = float(_bar_ex.close)
                                positions.append({"symbol": _sym_ex, "qty": qty_per_leg, "avg_price": _e_ex,
                                                   "strike": _s_ex, "opt_type": _rebal_ex, "bars": _brs_ex[_id_ex]})
                                _sm.add_owned_symbol(underlying, _sym_ex, replay_id)
                                fills_log.append({"time": bar_ts.strftime("%H:%M"), "symbol": _sym_ex,
                                                   "action": "SELL", "qty": abs(qty_per_leg), "price": _e_ex})
                                print(f"  [POST-EXIT REBAL] closed {inst.split(".")[0][-2:]} → ADD {_rebal_ex} {_sym_ex} @ {_e_ex} ({rebalance_otm_steps} steps OTM)")
                                rebalance_otm_steps = max(1, rebalance_otm_steps - 1)
                                if "CE" in _rebal_ex: ce_exit_spot = None
                                elif "PE" in _rebal_ex: pe_exit_spot = None
                    break

        elif decision.action == "FULL_EXIT":
            for p in positions:
                if p["qty"] != 0:
                    exit_bar = _bar_at(p["bars"], bar_ts)
                    exit_ltp = float(exit_bar.close) if exit_bar else p["avg_price"]
                    realized_pnl += (p["avg_price"] - exit_ltp) * abs(p["qty"])
                    fills_log.append({
                        "time":     bar_ts.strftime("%H:%M"),
                        "symbol":   p["symbol"],
                        "action":   "BUY",
                        "qty":      abs(p["qty"]),
                        "price":    exit_ltp,
                        "avg_price": p["avg_price"],  # entry price for P&L computation
                    })
                    p["qty"] = 0
            _sm.clear_owned_symbols(underlying, replay_id)
            exec_detail = f"[REPLAY] FULL_EXIT — all positions closed"
            executed = True
            print(f"  {exec_detail}")

        # Step 4: Log decision
        _log_decision(goal_dict, ctx, decision, executed, exec_detail, source, replay_id, bar_ts=bar_ts)

        # Step 5: Session memory narrative
        _sm.append_decision(
            underlying=underlying,
            ctx={"underlying_price": ctx.underlying_price, "pnl_inr": ctx.current_pnl},
            decision=decision.model_dump(),
            executed=executed,
            source=source,
            strategy_id=replay_id,
            timestamp=bar_ts,
        )

        # Track previous bar state for loss-mode center-crossing detection
        prev_spot    = ctx.underlying_price
        prev_in_loss = ctx.current_pnl < 0
        peak_pnl     = max(peak_pnl, ctx.current_pnl)
        # Update bars_beyond_be counter (handles 0, 1, or 2 BEs)
        from position_manager import _compute_payoff_metrics as _cpm_br
        _realized_br = ctx.current_pnl - sum(p.pnl for p in ctx.positions)
        _pm_br = _cpm_br(ctx.positions, ctx.underlying_price, getattr(ctx, "bar_dt", None), _realized_br)
        _be_dn_br = _pm_br.get("breakeven_down")
        _be_up_br = _pm_br.get("breakeven_up")
        _spot_br   = ctx.underlying_price
        _outside_be_br = (
            (_be_dn_br and _spot_br < _be_dn_br) or
            (_be_up_br and _spot_br > _be_up_br)
        )
        if _be_dn_br or _be_up_br:
            bars_beyond_be = (bars_beyond_be + 1) if _outside_be_br else 0
        else:
            bars_beyond_be = 0
        # When P&L recovers to >= 0 reset loss-mode tracking
        if ctx.current_pnl >= 0:
            last_loss_add_type  = None
            loss_otm_steps      = otm_steps - 1  # back to first-add level
            rebalance_otm_steps = otm_steps - 1
            bars_beyond_be      = 0
        _rb_ces = [p for p in positions if p["qty"] != 0 and "CE" in p["symbol"].upper()]
        _rb_pes = [p for p in positions if p["qty"] != 0 and "PE" in p["symbol"].upper()]
        if _rb_ces and _rb_pes:
            rebalance_safe_done = False

        # Track previous bar state for loss-mode center-crossing detection
        prev_spot    = ctx.underlying_price
        prev_in_loss = ctx.current_pnl < 0
        # When P&L recovers to >= 0 reset loss-mode tracking
        if ctx.current_pnl >= 0:
            last_loss_add_type = None
            loss_otm_steps     = otm_steps - 1  # back to first-add level

        # Step 5b: Write live state for dashboard display
        live_positions = []
        for ps in ctx.positions:
            if ps.otm_pct is not None:
                if ps.otm_pct < 0.005:   st = "CRITICAL"
                elif ps.otm_pct < 0.01:  st = "DANGER"
                elif ps.otm_pct < 0.02:  st = "WARNING"
                else:                     st = "OK"
            else:
                st = "OK"
            live_positions.append({
                "symbol":   ps.symbol,
                "opt_type": "CE" if ps.symbol.endswith("CE") else "PE",
                "qty":      ps.qty,
                "avg":      ps.avg_price,
                "ltp":      ps.ltp,
                "pnl":      ps.pnl,
                "otm_pct":  ps.otm_pct,
                "status":   st,
                "ratio":    ps.premium_ratio,
            })
        _sm.update_live_state(underlying, replay_id, {
            "hist_date":   replay_date_str,
            "hist_time":   bar_ts.strftime("%H:%M IST"),
            "bar_num":     bar_idx + 1,
            "total_bars":  n_bars,
            "spot":        round(spot, 2),
            "positions":   live_positions,
            "net_pnl":     round(ctx.current_pnl, 2),
            "fills":       fills_log,
            "expiry":      expiry,
            "ce_exit_spot": ce_exit_spot,
            "pe_exit_spot": pe_exit_spot,
        })

        # Step 6: Sleep        # Step 6: Sleep (simulates real-time playback)
        if bar_idx < n_bars - 1:
            next_bar_ts = datetime.fromtimestamp(all_bars[bar_idx+1].ts_event/1e9,
                                                  tz=timezone.utc).astimezone(IST)
            print(f"  Next bar: {next_bar_ts.strftime('%H:%M IST')} — sleeping {speed_secs}s")
            time.sleep(speed_secs)

    print(f"\n{'='*60}")
    print(f"Replay complete — {n_bars} bars processed")
    _u = get_llm_usage()
    _cost_usd = _u["cost_usd"]
    print(f"\nLLM Usage (GPT-4o):")
    print(f"  Calls         : {_u['calls']}")
    print(f"  Input tokens  : {_u['prompt_tokens']:,}")
    print(f"  Output tokens : {_u['completion_tokens']:,}")
    print(f"  Est. cost     : ${_cost_usd:.4f}  (~Rs.{_cost_usd*83:.2f})")
    print(f"View session: https://trading.34-45-46-60.sslip.io/?underlying=NIFTY&strategy_id={replay_id}")
    # Mark session as completed so dashboard shows correct status
    _d = _sm._load(underlying, replay_id)
    _d.setdefault("header", {})["status"] = "completed"
    _sm._save(underlying, replay_id, _d)
    print(f"{'='*60}\n")


# ── CLI ────────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="NIFTY options backtest replay")
    parser.add_argument("--date",  required=True, help="Historical date YYYY-MM-DD")
    parser.add_argument("--speed", type=int, default=60,
                        help="Seconds per 5-min bar (default 60; use 10 for fast test)")
    parser.add_argument("--lots",  type=int, default=5, help="Lots per leg (default 5)")
    parser.add_argument("--otm",    type=int, default=6,
                        help="OTM steps from ATM (default 6 = 300pts for NIFTY)")
    parser.add_argument("--no-llm", action="store_true", default=False,
                        help="Skip LLM calls — pure rule-based decisions only")
    parser.add_argument("--screen-name", default="",
                        help="Screen session name stored in stub for dashboard tracking")
    args = parser.parse_args()
    run_replay(args.date, args.speed, args.lots, args.otm,
               use_llm=not args.no_llm, screen_name=args.screen_name)


if __name__ == "__main__":
    main()
