"""
mcx_strangle.py — Short strangle manager for MCX commodities (GOLDM etc.)

Differences from NSE position_manager:
  - Exchange: MCX (not NFO)
  - Spot: near-month futures price (no index quote)
  - Product: NRML (MCX commodity positions)
  - Strike step: 500 for GOLDM
  - Lot size: 1 (10g per lot for GOLDM)
  - Trading hours: 09:00 - 23:30 IST (use --exit-time to cap)

HOW TO RUN (paper trade, analyze mode ON):
  cd ~/autotrade
  source .env
  .venv/bin/python agents/mcx_strangle.py \
    --underlying GOLDM --lots 5 \
    --target 5000 --max-loss -3000 \
    --expiry 2026-06-26 --entry-price 153204

STRATEGY:
  Sell OTM CE + OTM PE on MCX GOLDM.
  Agent checks every 5 min. Theta capture with stop-loss.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import requests
from openai import OpenAI

IST = timezone(timedelta(hours=5, minutes=30))
OPENALGO_BASE = "http://localhost:5000"
LOG_DIR = Path("/home/freed/autotrade/data/decision_logs")
DB_PATH = "/home/freed/openalgo/db/openalgo.db"

MCX_CONFIG = {
    "GOLDM": {
        "strike_step": 500,
        "lot_size": 1,
        "exchange": "MCX",
        "product": "NRML",
        "min_otm_pct": 0.012,
        "target_premium_min": 500,
        "target_premium_max": 2000,
    }
}

SYSTEM_PROMPT_MCX = """You are an expert MCX commodity options manager for short strangles on GOLDM (Gold Mini, 10g per lot).

Gold moves differ from equity: driven by USD/INR, global events, US inflation data.
Intraday GOLDM range: Rs.500-1500 normal / Rs.2000-4000 volatile.
IV spikes at MCX open (09:00), US data releases (14:30, 21:30 IST).

=== HARD RULES ===
- Any leg loss > -Rs.3000: close that leg immediately (PARTIAL_EXIT)
- Any leg OTM < 0.5%: close immediately
- Exit by the session exit time (hard stop)

=== MANAGEMENT RULES ===
1. P&L <= max_loss: FULL_EXIT
2. P&L >= 60% of target: PARTIAL_EXIT or FULL_EXIT (lock profit)
3. Any leg OTM < 1.0%: SHIFT_STRIKE (roll further OTM)
4. Premium ratio > 2.0 (premium doubled): close or roll
5. Spot moved > Rs.1500 from intraday_high or intraday_low: FULL_EXIT (trend day, gold following macro)
6. After 21:00 IST with negative P&L: FULL_EXIT (avoid overnight risk)
7. Default: HOLD (theta working)

For ADD_POSITION: set instrument="CE" or instrument="PE", direction="SELL", quantity=<lots>. Do NOT construct symbols.
For SHIFT_STRIKE: set instrument=exact current symbol to close, direction="BUY", quantity=current qty.

Output strict JSON only:
{"action":"HOLD|SHIFT_STRIKE|ADD_POSITION|PARTIAL_EXIT|FULL_EXIT","instrument":null,"quantity":null,"direction":null,"price_type":"MARKET","price":null,"reasoning":"one sentence with rule","urgency":"low|medium|high","next_review":"5min|15min|30min"}
"""

_OPT_RE = re.compile(r"^([A-Z]+?)(\d{2})([A-Z]{3})(\d{2})(\d+)(CE|PE)$")


def _headers(api_key: str) -> dict:
    return {"x-api-key": api_key, "Content-Type": "application/json"}


def _get_spot_mcx(api_key: str, underlying: str) -> float:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT symbol FROM symtoken WHERE exchange='MCX' AND symbol LIKE ? "
        "AND instrumenttype='FUT' ORDER BY expiry LIMIT 1",
        (f"{underlying}%FUT",)
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        return 0.0
    try:
        r = requests.post(f"{OPENALGO_BASE}/api/v1/quotes",
                          json={"apikey": api_key, "symbol": row[0], "exchange": "MCX"},
                          headers=_headers(api_key), timeout=8)
        r.raise_for_status()
        d = r.json()
        inner = d.get("data", d) if isinstance(d, dict) else {}
        return float(inner.get("ltp", 0))
    except Exception:
        return 0.0


def _get_positions_mcx(api_key: str) -> list[dict]:
    try:
        r = requests.post(f"{OPENALGO_BASE}/api/v1/positionbook",
                          json={"apikey": api_key},
                          headers=_headers(api_key), timeout=8)
        r.raise_for_status()
        d = r.json()
        raw = d.get("data", d) if isinstance(d, dict) else d
        return [p for p in (raw or []) if p.get("exchange") == "MCX" and int(p.get("quantity", 0)) != 0]
    except Exception:
        return []


def _place_order_mcx(api_key: str, symbol: str, action: str, qty: int) -> dict:
    payload = {"apikey": api_key, "strategy": "MCXStrangle",
               "symbol": symbol, "action": action,
               "exchange": "MCX", "pricetype": "MARKET",
               "product": "NRML", "quantity": str(qty)}
    r = requests.post(f"{OPENALGO_BASE}/api/v1/placeorder",
                      json=payload, headers=_headers(api_key), timeout=10)
    r.raise_for_status()
    return r.json()


def _build_sym(underlying: str, expiry: str, strike: int, opt_type: str) -> str:
    dt = datetime.strptime(expiry, "%Y-%m-%d")
    return f"{underlying}{dt.strftime('%d%b%y').upper()}{strike}{opt_type}"


def _get_ltp(api_key: str, symbol: str) -> float:
    try:
        r = requests.post(f"{OPENALGO_BASE}/api/v1/quotes",
                          json={"apikey": api_key, "symbol": symbol, "exchange": "MCX"},
                          headers=_headers(api_key), timeout=5)
        r.raise_for_status()
        d = r.json()
        inner = d.get("data", d) if isinstance(d, dict) else {}
        return float(inner.get("ltp", 0))
    except Exception:
        return 0.0


def _scan_strike_mcx(api_key: str, underlying: str, expiry: str, spot: float, opt_type: str) -> int:
    cfg = MCX_CONFIG.get(underlying, MCX_CONFIG["GOLDM"])
    step = cfg["strike_step"]
    atm = round(spot / step) * step
    min_otm_pts = int(spot * cfg["min_otm_pct"])
    min_otm_rounded = max(round(min_otm_pts / step) * step, step)
    if opt_type == "CE":
        start, direction = atm + min_otm_rounded, 1
    else:
        start, direction = atm - min_otm_rounded, -1
    for i in range(20):
        strike = start + direction * i * step
        ltp = _get_ltp(api_key, _build_sym(underlying, expiry, strike, opt_type))
        if ltp <= 0:
            continue
        if ltp > cfg["target_premium_max"]:
            continue
        if ltp >= cfg["target_premium_min"]:
            return strike
        break
    return start


def _log(goal, ctx, decision, executed, detail):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(IST).strftime("%Y-%m-%d")
    record = {"ts": datetime.now(IST).isoformat(), "goal": goal, "context_summary": ctx,
              "decision": decision, "executed": executed, "execution_detail": detail, "source": "mcx_strangle"}
    with open(LOG_DIR / f"{today}-mcx.jsonl", "a") as f:
        f.write(json.dumps(record) + "\n")


def _call_llm(client, args, spot, pnl, positions, intraday_high, intraday_low):
    ctx = {"underlying": args.underlying, "spot": spot, "entry_price": args.entry_price,
           "move_from_entry": round(spot - args.entry_price, 2),
           "move_from_intraday_high": round(spot - intraday_high, 2),
           "move_from_intraday_low": round(spot - intraday_low, 2),
           "intraday_high": intraday_high, "intraday_low": intraday_low,
           "session_pnl": pnl, "target": args.target, "max_loss": args.max_loss,
           "expiry": args.expiry, "time_ist": datetime.now(IST).strftime("%H:%M"),
           "positions": [{"symbol": p.get("symbol"), "qty": p.get("quantity"),
                          "avg": p.get("average_price"), "ltp": p.get("ltp"),
                          "pnl": p.get("pnl")} for p in positions]}
    resp = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "system", "content": SYSTEM_PROMPT_MCX},
                  {"role": "user", "content": f"State:\n{json.dumps(ctx, indent=2)}\nAction?"}],
        temperature=0.1,
        response_format={"type": "json_object"},
    )
    return json.loads(resp.choices[0].message.content)


def main():
    ap = argparse.ArgumentParser(description="MCX commodity short strangle manager")
    ap.add_argument("--underlying",   default="GOLDM")
    ap.add_argument("--lots",         type=int,   default=5)
    ap.add_argument("--target",       type=float, default=5000.0)
    ap.add_argument("--max-loss",     type=float, default=-3000.0)
    ap.add_argument("--expiry",       required=True,  help="YYYY-MM-DD")
    ap.add_argument("--entry-price",  type=float, required=True, help="GOLDM spot at entry")
    ap.add_argument("--exit-time",    default="23:00", help="HH:MM IST force exit")
    ap.add_argument("--cycle",        type=int,   default=300)
    ap.add_argument("--dry-run",      action="store_true")
    args = ap.parse_args()

    api_key   = os.environ.get("OPENALGO_API_KEY", "")
    openai_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key or not openai_key:
        sys.exit("Set OPENALGO_API_KEY and OPENAI_API_KEY env vars")

    client = OpenAI(api_key=openai_key)
    cfg    = MCX_CONFIG.get(args.underlying, MCX_CONFIG["GOLDM"])
    exit_h, exit_m = map(int, args.exit_time.split(":"))
    goal = {"underlying": args.underlying, "lots": args.lots, "target": args.target,
            "max_loss": args.max_loss, "expiry": args.expiry}

    print(f"\nMCX Strangle — {args.underlying}  target Rs.{args.target:,.0f}  floor Rs.{args.max_loss:,.0f}")
    print(f"  Lots: {args.lots}  |  Expiry: {args.expiry}  |  Exit by: {args.exit_time} IST")
    print(f"  Dry-run: {args.dry_run}  |  Cycle: {args.cycle}s\n")

    intraday_high = args.entry_price
    intraday_low  = args.entry_price
    session_closed = False
    cycle = 0

    while True:
        cycle += 1
        now = datetime.now(IST)
        print(f"Cycle {cycle}  {now.strftime('%H:%M IST')}")

        if session_closed:
            print("  Session closed — done for today")
            break

        spot      = _get_spot_mcx(api_key, args.underlying)
        positions = _get_positions_mcx(api_key)
        pnl       = sum(float(p.get("pnl", 0)) for p in positions)
        intraday_high = max(intraday_high, spot)
        intraday_low  = min(intraday_low,  spot)

        print(f"  Spot: {spot:,.0f}  High: {intraday_high:,.0f}  Low: {intraday_low:,.0f}  P&L: Rs.{pnl:+,.0f}  Positions: {len(positions)}")

        # Hard-stop rules
        decision = None
        source    = "rules"
        now_mins  = now.hour * 60 + now.minute
        exit_mins = exit_h * 60 + exit_m

        if pnl <= args.max_loss and positions:
            decision = {"action": "FULL_EXIT", "reasoning": "Max loss floor hit",
                        "urgency": "high", "next_review": "5min",
                        "instrument": None, "quantity": None, "direction": None, "price_type": "MARKET", "price": None}
            session_closed = True
        elif now_mins >= exit_mins and positions:
            decision = {"action": "FULL_EXIT", "reasoning": f"Exit time {args.exit_time} reached",
                        "urgency": "medium", "next_review": "5min",
                        "instrument": None, "quantity": None, "direction": None, "price_type": "MARKET", "price": None}
            session_closed = True

        if decision is None:
            source = "llm"
            try:
                decision = _call_llm(client, args, spot, pnl, positions, intraday_high, intraday_low)
            except Exception as e:
                print(f"  LLM error: {e} — HOLD")
                decision = {"action": "HOLD", "reasoning": f"LLM error: {e}",
                            "urgency": "low", "next_review": "5min",
                            "instrument": None, "quantity": None, "direction": None, "price_type": "MARKET", "price": None}

        action = decision.get("action", "HOLD")
        print(f"  [{source}] {action}  — {decision.get('reasoning','')[:80]}")

        executed    = False
        exec_detail = "dry_run"

        if not args.dry_run:
            try:
                if action == "FULL_EXIT":
                    for p in positions:
                        sym = p.get("symbol", "")
                        qty = abs(int(p.get("quantity", 0)))
                        close_dir = "BUY" if int(p.get("quantity", 0)) < 0 else "SELL"
                        r = _place_order_mcx(api_key, sym, close_dir, qty)
                        print(f"    Closed {sym}: {r.get('orderid', r)}")
                    executed = True
                    exec_detail = "FULL_EXIT executed"
                    if action == "FULL_EXIT":
                        session_closed = True

                elif action == "ADD_POSITION":
                    opt_type = (decision.get("instrument") or "").upper()
                    if opt_type in ("CE", "PE"):
                        strike = _scan_strike_mcx(api_key, args.underlying, args.expiry, spot, opt_type)
                        sym    = _build_sym(args.underlying, args.expiry, strike, opt_type)
                        qty    = args.lots * cfg["lot_size"]
                        r      = _place_order_mcx(api_key, sym, "SELL", qty)
                        print(f"    ADD {sym} SELL {qty}: {r.get('orderid', r)}")
                        executed    = True
                        exec_detail = f"ADD_POSITION {sym}"
                    else:
                        exec_detail = f"ADD_POSITION: instrument must be CE or PE, got {decision.get('instrument')}"

                elif action == "PARTIAL_EXIT" and decision.get("instrument"):
                    sym = decision["instrument"]
                    qty = int(decision.get("quantity") or args.lots * cfg["lot_size"])
                    r   = _place_order_mcx(api_key, sym, "BUY", qty)
                    executed    = True
                    exec_detail = f"PARTIAL_EXIT {sym}: {r.get('orderid', r)}"

                elif action == "SHIFT_STRIKE" and decision.get("instrument"):
                    sym = decision["instrument"]
                    qty = int(decision.get("quantity") or args.lots * cfg["lot_size"])
                    m   = _OPT_RE.match(sym.upper())
                    if m:
                        opt_type = m.group(6)
                        r1 = _place_order_mcx(api_key, sym, "BUY", qty)
                        new_strike = _scan_strike_mcx(api_key, args.underlying, args.expiry, spot, opt_type)
                        new_sym    = _build_sym(args.underlying, args.expiry, new_strike, opt_type)
                        r2 = _place_order_mcx(api_key, new_sym, "SELL", qty)
                        print(f"    ROLL: {sym} -> {new_sym}")
                        executed    = True
                        exec_detail = f"Roll {sym} -> {new_sym}"
            except Exception as e:
                exec_detail = f"Execution error: {e}"
                print(f"  ERROR: {e}")

        _log(goal, {"spot": spot, "pnl": pnl, "intraday_high": intraday_high, "intraday_low": intraday_low}, decision, executed, exec_detail)

        next_at = datetime.now(IST) + timedelta(seconds=args.cycle)
        print(f"  Next at {next_at.strftime('%H:%M:%S IST')}")
        time.sleep(args.cycle)

    print("\nMCX Strangle loop ended.")


if __name__ == "__main__":
    main()
