"""
position_manager.py — Stage 7: Goal-Directed Intraday Position Management Agent.

Runs every 1 minute during market hours (9:15am–3:30pm IST).
GPT-4o reasons toward a defined goal using live market context.

Architecture:
  1. HARD STOP (always first, outside LLM) — instant, deterministic
  2. BEHAVIORAL CHECKS (pre-LLM rule engine) — fires before LLM on hard triggers
  3. Build ContextSnapshot from OpenAlgo + Zerodha OI + VIX
  4. Call LLM with goal + behavioral rules + context → structured Decision JSON
  5. Execute decision via OpenAlgo REST
  6. Log everything for post-market reflection (Stage 8)
  7. Sleep 60 seconds

HOW TO RUN (paper trade session):
  cd ~/autotrade
  .venv/bin/python agents/position_manager.py \
    --underlying NIFTY \
    --target 6000 \
    --max-loss -8000 \
    --expiry 2026-06-23 \
    --entry-price 24200

  Or dry-run (no orders placed):
  .venv/bin/python agents/position_manager.py --dry-run ...
"""
from __future__ import annotations

import argparse
import httpx
import json
import os
import sys
import time
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from openai import OpenAI

sys.path.insert(0, str(Path(__file__).parent))
from goal_schema import Decision, Goal, ContextSnapshot, PositionSnapshot
from context_builder import build_context
from decision_executor import execute
from decision_logger import log_decision
import session_memory as _sm

IST = timezone(timedelta(hours=5, minutes=30))

# NSE/NFO market hours
NSE_MARKET_OPEN  = (9, 15)
NSE_MARKET_CLOSE = (15, 30)

# MCX commodity market hours (metals/energy trade until 23:30 IST)
MCX_MARKET_OPEN  = (9, 0)
MCX_MARKET_CLOSE = (23, 30)

_MCX_UNDERLYINGS = {"GOLDM", "GOLD", "SILVER", "CRUDEOIL", "NATURALGAS"}

# Backward-compat aliases (NSE defaults)
MARKET_OPEN  = NSE_MARKET_OPEN
MARKET_CLOSE = NSE_MARKET_CLOSE

CYCLE_SECONDS = 300  # 5-minute loop

LLM_MODEL = "gpt-4o"

# ── Behavioural risk constants (empirical — 17 session analysis) ─────────────
MAX_SINGLE_POSITION_LOSS = 6_000    # Rs — hard close any leg losing more than this

_llm_usage: dict = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}

def get_llm_usage() -> dict:
    """Return cumulative LLM token usage (GPT-4o: $2.50/1M in, $10.00/1M out)."""
    u = _llm_usage.copy()
    u["cost_usd"] = round(u["prompt_tokens"]/1e6*2.50 + u["completion_tokens"]/1e6*10.00, 5)
    return u
CRITICAL_OTM_PCT = 0.005            # <0.5% OTM → close immediately, no questions
DANGER_OTM_PCT   = 0.010            # <1.0% OTM → high urgency flag to LLM
WARNING_OTM_PCT  = 0.015            # <1.5% OTM → warning flag to LLM
DOUBLE_RULE_RATIO = 2.0             # premium_ratio > 2 → mandatory LLM review

# NSE exit time windows by DTE
DTE_EXIT_TIMES = {
    0: (15, 0),    # expiry day: hold until 15:00
    1: (11, 0),    # 1 DTE: exit by 11:00
    2: (13, 0),    # 2-4 DTE: exit by 13:00
    3: (13, 0),
    4: (13, 0),
}
DTE_EXIT_TIME_DEFAULT = (12, 30)    # NSE 5+ DTE: exit by 12:30

# MCX exit times — commodities have evening session, hold longer
MCX_DTE_EXIT_TIMES = {
    0: (16, 30),   # MCX expiry day: exit by 16:30
    1: (23, 30),   # 1 DTE: hold until 23:30
    2: (23, 30),   # 2-4 DTE: hold until 23:30
    3: (23, 30),
    4: (23, 30),
}
MCX_DTE_EXIT_TIME_DEFAULT = (23, 30)    # MCX 5+ DTE: hold until 23:30


# ── System prompt ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are an expert Indian options intraday position manager for short strangles on NIFTY/BANKNIFTY/SENSEX (NSE) and GOLDM (MCX). Respond ONLY with valid JSON, no markdown.

=== YOUR ROLE ===
ALL exits and position adds are handled by code rules before you are called.
If you are called, it means no rule triggered. Your ONLY job: return HOLD with a one-sentence explanation of the current market state.
DEFAULT: HOLD. You MUST NOT return PARTIAL_EXIT or FULL_EXIT — those are handled by code.

=== RULES ALREADY HANDLED BY CODE (do not repeat these) ===
- Leg P&L < -Rs.6000: auto-closed
- Leg OTM% < 0.5% (R3): auto-closed
- Leg premium_ratio > 2.0 (R2): auto-closed
- P&L <= max_loss (R1): auto full-exit
- Spot approaching breakeven: auto ADD_POSITION to opposite leg

=== YOUR DECISION ===
Return HOLD. In "reasoning": one sentence describing what you observe — spot direction, which leg has most pressure, whether theta is decaying favorably.
DO NOT invent rule triggers. DO NOT return exits. DO NOT add positions unless you observe a strong directional move that the code clearly has not addressed yet.

  "action": "HOLD",
  "instrument": null,
  "quantity": null,
  "direction": null,
  "price_type": "MARKET",
  "price": null,
  "reasoning": "one sentence observation of current market state",
  "urgency": "low",
  "next_review": "5min"
}
"""


# ── Equity strategy prompt ─────────────────────────────────────────────────────

EQUITY_SYSTEM_PROMPT = """You are an expert Indian equity intraday/positional trade manager for NSE cash market stocks. Respond ONLY with valid JSON, no markdown.

=== CORE GOAL ===
Protect capital first, then maximise profit. A loss should be avoided at all costs.
Entry is already done. Your job: manage the open position to the goal or stop.

=== RULE SET A: POSITION STATE ===
A1. Goal fields in context: direction (LONG/SHORT), entry_price, target_price, stop_loss_price,
    trailing_stop_pct, qty, target_profit (INR), max_loss (INR).
A2. current_pnl = (ltp - entry_price) * qty  [LONG] or  (entry_price - ltp) * qty  [SHORT]
A3. OTM% equivalent for equity = (target_price - ltp) / ltp  [how far from target]

=== RULE SET B: STOP MANAGEMENT ===
B1. Hard stop: if current_pnl <= max_loss -> FULL_EXIT immediately. No discussion.
B2. Trailing stop activation: once position is profitable > 0.5%, activate trailing.
    New stop = max(stop_loss_price, ltp * (1 - trailing_stop_pct))  [LONG]
    New stop = min(stop_loss_price, ltp * (1 + trailing_stop_pct))  [SHORT]
    Use MODIFY_STOP to update new_stop_price each time trailing advances.
B3. Stop breach: if ltp crosses stop_loss_price -> FULL_EXIT immediately.
B4. Never widen a stop to avoid an exit. Stops only move in the profitable direction.

=== RULE SET C: TARGET MANAGEMENT ===
C1. If ltp >= target_price (LONG) or ltp <= target_price (SHORT): PARTIAL_EXIT 50% qty first.
    Move stop to breakeven. Let remaining half run with tighter trailing.
C2. After partial exit at target, if ltp continues in direction: FULL_EXIT at 1.5x original target distance.
C3. Reversal signal: if position dropped >1% from intraday high (LONG) or rose >1% from intraday low (SHORT)
    -> PARTIAL_EXIT half to protect gains even if target not hit.

=== RULE SET D: SCALING ===
D1. Scale in only if: position is already profitable (>0.3% in favour), clear trend continuation,
    ADD_POSITION adds at most 50% of original qty. Max 2 scale-ins per session.
D2. Never scale into a losing trade (price has moved against entry).

=== RULE SET E: INTRADAY CONTEXT ===
E1. After 14:30 IST: only defensive actions. No new scale-ins. Prepare to exit before 15:20.
E2. If position held overnight (product=CNC): EOD review. Apply same rules at open next day.

=== DECISION FRAMEWORK ===
1. current_pnl <= max_loss? -> FULL_EXIT
2. Stop price breached? -> FULL_EXIT
3. Partial target hit (C1)? -> PARTIAL_EXIT 50%
4. Trailing stop advance (B2)? -> MODIFY_STOP
5. After-14:30 and still in profit? -> FULL_EXIT to lock gains
6. Scale-in signal (D1)? -> ADD_POSITION
7. All good -> HOLD

Output schema (strict JSON):
{
  "action": "HOLD|ADD_POSITION|PARTIAL_EXIT|FULL_EXIT|MODIFY_STOP",
  "instrument": null,
  "quantity": null,
  "direction": null,
  "price_type": "MARKET",
  "price": null,
  "new_stop_price": null,
  "reasoning": "one sentence: rule triggered",
  "urgency": "low|medium|high",
  "next_review": "5min|15min|30min"
}

FIELD RULES:
- ADD_POSITION: instrument=stock_symbol (e.g. "RELIANCE"), direction="BUY" or "SELL", quantity=qty to add.
- PARTIAL_EXIT: instrument=stock_symbol, direction opposite to position (LONG->SELL, SHORT->BUY), quantity=qty to close.
- FULL_EXIT: instrument=stock_symbol, direction opposite to position, quantity=total open qty.
- MODIFY_STOP: new_stop_price=updated stop level. Other fields null.
- HOLD: all optional fields null.
"""

# ── Futures strategy prompt ────────────────────────────────────────────────────

FUTURES_SYSTEM_PROMPT = """You are an expert Indian futures intraday/positional trade manager for NSE/MCX futures. Respond ONLY with valid JSON, no markdown.

=== CORE GOAL ===
Protect capital first, maximise profit second. Leverage amplifies both — respect the stop.

=== RULE SET A: POSITION STATE ===
A1. Goal fields: direction (LONG/SHORT), lots, entry_price, target_price, stop_loss_price,
    trailing_stop_pct, target_profit (INR), max_loss (INR).
A2. Lot sizes: NIFTY=75, BANKNIFTY=15, GOLDM=1, GOLD=1, CRUDEOIL=100.
    PnL per point = lot_size * lots. Always think in % of entry_price, not absolute points.
A3. Leverage context: 1% move on NIFTY FUT = Rs.720+ per lot. Stops must be tight.

=== RULE SET B: STOP AND TARGET ===
B1. Hard stop breach -> FULL_EXIT immediately.
B2. Trailing stop: advance stop as position profits, same as equity B2.
    For futures, trail by 0.5% minimum step to avoid noise exits.
B3. Target hit: PARTIAL_EXIT 50%, move stop to breakeven, trail remaining.
B4. Never widen stop. Never average into losing futures position.

=== RULE SET C: LEVERAGE RISK ===
C1. If position has moved >0.8% against entry price: review urgency=high. Consider partial exit.
C2. MCX commodities: check for gap opens and circuit events. If ltp deviates >2% from entry
    in one cycle (large gap): FULL_EXIT - data may be stale, protect capital.
C3. Expiry day (DTE=0): FULL_EXIT before 15:20 NSE / 23:00 MCX. Do not hold through expiry.

=== RULE SET D: SCALING ===
D1. Scale in only if profitable >0.5%, trend confirmed (3 consecutive candles in direction),
    adding at most 1 lot at a time. Max 2 scale-ins.
D2. Never add to a losing futures position.

=== DECISION FRAMEWORK ===
1. current_pnl <= max_loss? -> FULL_EXIT
2. Stop price breached? -> FULL_EXIT
3. C2 gap/circuit event? -> FULL_EXIT
4. Target hit (B3)? -> PARTIAL_EXIT
5. Trailing stop advance? -> MODIFY_STOP
6. C1 adverse move >0.8%? -> review, likely PARTIAL_EXIT
7. Scale-in signal (D1)? -> ADD_POSITION
8. All good -> HOLD

Output schema (strict JSON):
{
  "action": "HOLD|ADD_POSITION|PARTIAL_EXIT|FULL_EXIT|MODIFY_STOP",
  "instrument": null,
  "quantity": null,
  "direction": null,
  "price_type": "MARKET",
  "price": null,
  "new_stop_price": null,
  "reasoning": "one sentence: rule triggered",
  "urgency": "low|medium|high",
  "next_review": "1min|5min|15min"
}

FIELD RULES:
- ADD_POSITION: instrument=futures_symbol (e.g. "GOLDM25JULFUT" or "NIFTY25JUNFUT"), direction="BUY"/"SELL", quantity=lot_size (one lot worth of contracts).
- PARTIAL_EXIT: instrument=futures_symbol, direction opposite to position, quantity=qty to close.
- FULL_EXIT: instrument=futures_symbol, direction opposite, quantity=all open qty.
- MODIFY_STOP: new_stop_price=updated stop. Other fields null.
- HOLD: all optional fields null.
"""



# ── Helper: is market open ─────────────────────────────────────────────────────

def _is_market_hours(underlying: str = "NIFTY") -> bool:
    now = datetime.now(IST)
    if underlying.upper() in _MCX_UNDERLYINGS:
        open_t, close_t = MCX_MARKET_OPEN, MCX_MARKET_CLOSE
    else:
        open_t, close_t = NSE_MARKET_OPEN, NSE_MARKET_CLOSE
    open_mins  = open_t[0]  * 60 + open_t[1]
    close_mins = close_t[0] * 60 + close_t[1]
    now_mins   = now.hour * 60 + now.minute
    return open_mins <= now_mins <= close_mins


def _load_env():
    env_path = Path("/home/freed/autotrade/.env")
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip())


# ── Hard stop ────────────────────────────────────────────────────────────────

def hard_stop_check(current_pnl: float, max_loss: float, api_key: str, dry_run: bool) -> bool:
    """Portfolio-level hard floor. Always runs before everything else."""
    if current_pnl <= max_loss:
        print(f"\n{'='*60}")
        print(f"HARD STOP: P&L Rs.{current_pnl:,.0f} <= floor Rs.{max_loss:,.0f}")
        print("="*60)
        if not dry_run:
            from decision_executor import _close_all_positions
            results = _close_all_positions(api_key)
            print(f"Closed all: {results}")
            _send_telegram(f"HARD STOP: P&L Rs.{current_pnl:,.0f} hit floor Rs.{max_loss:,.0f}. All positions closed.")
        else:
            print("[DRY RUN] Would close all positions")
        return True
    return False


# ── Deterministic pre-LLM behavioural checks ─────────────────────────────────

def behavioral_checks(ctx: ContextSnapshot, goal: Goal,
                      prev_spot: float = None,
                      last_loss_add_type: str = None,
                      loss_otm_steps: int = 6,
                      prev_in_loss: bool = False,
                      peak_pnl: float = 0.0,
                      rebalance_otm_steps: int = 5,
                      bars_beyond_be: int = 0,
                      rebalance_safe_done: bool = False,
                      ce_exit_spot: float = None,
                      pe_exit_spot: float = None) -> Optional[Decision]:
    """
    Fires hard rules BEFORE calling the LLM. Returns a Decision if a rule
    triggers, otherwise returns None (→ proceed to LLM for judgment).

    Order matters — most urgent checks run first.
    """
    now = datetime.now(IST)
    # Realized P&L from closed legs = total pnl minus sum of open-leg unrealized pnl
    _realized_pnl_offset = ctx.current_pnl - sum(p.pnl for p in ctx.positions)

    # ── 1. Per-position max loss ───────────────────────────────────────────
    for p in ctx.positions:
        if p.qty < 0 and p.pnl < -MAX_SINGLE_POSITION_LOSS:
            print(f"  [RULE] Per-position max loss: {p.symbol} P&L Rs.{p.pnl:,.0f}")
            return Decision(
                action="PARTIAL_EXIT",
                instrument=p.symbol,
                quantity=abs(p.qty),
                direction="BUY",
                reasoning=(
                    f"Position {p.symbol} hit per-leg loss limit "
                    f"(Rs.{p.pnl:,.0f} < -Rs.{MAX_SINGLE_POSITION_LOSS:,})"
                ),
                urgency="high",
                next_review="1min",
            )

    # ── 2. Premium doubled (R2) — close immediately ───────────────────────
    for p in ctx.positions:
        _ratio = getattr(p, 'premium_ratio', None)
        if p.qty < 0 and _ratio is not None and _ratio > DOUBLE_RULE_RATIO:
            print(f"  [RULE] R2 Premium doubled: {p.symbol} ratio={_ratio:.2f}x")
            return Decision(
                action="PARTIAL_EXIT",
                instrument=p.symbol,
                quantity=abs(p.qty),
                direction="BUY",
                reasoning=(
                    f"R2: {p.symbol} premium_ratio {_ratio:.2f}x > {DOUBLE_RULE_RATIO}x "
                    f"(premium doubled) — mandatory close"
                ),
                urgency="high",
                next_review="1min",
            )

    # ── 3. CRITICAL OTM zone (<0.5%, R3) — close immediately ─────────────
    for p in ctx.positions:
        if p.qty < 0 and p.otm_pct is not None and p.otm_pct < CRITICAL_OTM_PCT:
            print(f"  [RULE] R3 Critical OTM: {p.symbol} OTM={p.otm_pct*100:.2f}%")
            return Decision(
                action="PARTIAL_EXIT",
                instrument=p.symbol,
                quantity=abs(p.qty),
                direction="BUY",
                reasoning=(
                    f"R3: {p.symbol} OTM {p.otm_pct*100:.2f}% < 0.5% threshold "
                    f"— close before going ITM"
                ),
                urgency="high",
                next_review="1min",
            )

    # ── 4. DTE computation (used by BE_RECENTER rule below) ──────────────
    # Time-based exit window removed: used datetime.now() which breaks replay.
    # LLM system prompt still advises closing by 12:30 for 5+DTE options.
    if goal.strategy_type in ("equity", "futures"):
        dte = None
    else:
        dte = ctx.positions[0].dte if ctx.positions and ctx.positions[0].dte is not None else None
        if dte is None and goal.expiry:
            try:
                expiry_dt = datetime.strptime(goal.expiry, "%Y-%m-%d").replace(tzinfo=None)
                dte = max((expiry_dt - now.replace(tzinfo=None)).days, 0)
            except Exception:
                dte = 0


    # ── REBALANCE: respond to one-sided strangle ────────────────────────────────
    # When all open legs are on one side (no CE or no PE):
    #   Spot within 2 strike-steps of entry  -> add MISSING side (market reverted, restore)
    #   Spot still away from entry            -> add SAME side as remaining (market trending,
    #                                            collect premium on the safe/untested side)
    _open_legs_rb = [p for p in ctx.positions if p.qty != 0]
    _open_ces_rb  = [p for p in _open_legs_rb if 'CE' in p.symbol.upper()]
    _open_pes_rb  = [p for p in _open_legs_rb if 'PE' in p.symbol.upper()]
    _one_sided_rb = bool(_open_legs_rb) and (not _open_ces_rb or not _open_pes_rb)
    if goal.strategy_type == 'options' and _one_sided_rb and ctx.current_pnl > goal.max_loss:
        _prof_rb     = _INSTRUMENT_PROFILES.get(goal.underlying.upper(), _INSTRUMENT_PROFILES['NIFTY'])
        _step_rb     = _prof_rb['strike_step']
        _lot_rb      = _prof_rb['lot']
        _entry_rb   = ctx.underlying_price - ctx.underlying_move_pts
        _dist_rb    = abs(ctx.underlying_price - _entry_rb)
        _missing_ce = not _open_ces_rb
        _missing_pe = not _open_pes_rb
        # Directional restore: CE missing → restore only when spot drops BELOW entry
        # (proves the upward move that killed CE has reversed); vice-versa for PE.
        _ce_ref_rb   = ce_exit_spot if ce_exit_spot else _entry_rb
        _pe_ref_rb   = pe_exit_spot if pe_exit_spot else _entry_rb
        _restore_ce  = _missing_ce and (ctx.underlying_price < _ce_ref_rb - _step_rb)
        _restore_pe  = _missing_pe and (ctx.underlying_price > _pe_ref_rb + _step_rb)
        if _restore_ce or _restore_pe:
            _add_rb    = 'CE' if _restore_ce else 'PE'
            _reason_rb = 'REBALANCE_RESTORE'
            _desc_rb   = (f'spot {ctx.underlying_price:.0f} crossed past entry {_entry_rb:.0f} '
                          f'({_dist_rb:.0f}pt) — restoring missing side')
        elif not rebalance_safe_done:
            _add_rb    = 'CE' if _open_ces_rb else 'PE'
            _reason_rb = 'REBALANCE_SAFE'
            _desc_rb   = (f'spot {ctx.underlying_price:.0f} {_dist_rb:.0f}pt from entry '
                          f'{_entry_rb:.0f} — adding safe side')
        else:
            _add_rb = None  # safe-side already added this incident; wait for RESTORE
        if _add_rb is not None:
            # Floor OTM so REBALANCE never adds a position R3 would immediately close
            # R3 closes when OTM < CRITICAL_OTM_PCT (0.5%). Keep 2 steps above that.
            _r3_floor_steps = int(CRITICAL_OTM_PCT * ctx.underlying_price / _step_rb) + 2
            _safe_steps_rb  = max(rebalance_otm_steps, _r3_floor_steps)
            _otm_rb = (_safe_steps_rb * _step_rb) / ctx.underlying_price
            _qty_rb = 1 * _lot_rb
            print(f'  [RULE] {_reason_rb}: {_desc_rb}, adding {_add_rb} @ '
                  f'{rebalance_otm_steps} steps OTM ({_otm_rb*100:.1f}%)')
            return Decision(
                action='ADD_POSITION', instrument=_add_rb,
                quantity=_qty_rb, direction='SELL', target_otm_pct=_otm_rb,
                reasoning=(
                    f'{_reason_rb}: {_desc_rb}. '
                    f'Adding {_add_rb} at {rebalance_otm_steps} steps OTM '
                    f'({_otm_rb*100:.1f}%) (spot {ctx.underlying_price:.0f}).'
                ),
                urgency='high', next_review='5min',
            )

    # ── SUSTAINED BE BREACH: add opposite side when stuck outside BE ────────────
    # Fires when spot has been beyond a breakeven for 3+ consecutive bars.
    # Adds on the OPPOSITE side, one step tighter than the closest existing
    # position on that side, to shift the profit zone toward current spot.
    _open_legs_sb = [p for p in ctx.positions if p.qty != 0]
    if (bars_beyond_be >= 3
            and goal.strategy_type == 'options'
            and len(_open_legs_sb) >= 2
            and ctx.current_pnl > goal.max_loss
            and ctx.bars_since_last_add >= 3):
        _pm_sb    = _compute_payoff_metrics(ctx.positions, ctx.underlying_price,
                                             getattr(ctx, "bar_dt", None), _realized_pnl_offset)
        _be_dn_sb = _pm_sb.get("breakeven_down")
        _be_up_sb = _pm_sb.get("breakeven_up")
        _spot_sb  = ctx.underlying_price
        _breach_side = None
        if _be_dn_sb and _spot_sb < _be_dn_sb:
            _breach_side = "down"   # below lower BE → add CE (opposite)
            _add_sb = "CE"
        elif _be_up_sb and _spot_sb > _be_up_sb:
            _breach_side = "up"     # above upper BE → add PE (opposite)
            _add_sb = "PE"
        elif not _be_up_sb and _be_dn_sb and _spot_sb < _be_dn_sb:
            # Only lower BE found (curve not fully positive on upper side)
            _breach_side = "down"
            _add_sb = "CE"
        elif not _be_dn_sb and _be_up_sb and _spot_sb > _be_up_sb:
            _breach_side = "up"
            _add_sb = "PE"
        if _breach_side:
            _prof_sb  = _INSTRUMENT_PROFILES.get(goal.underlying.upper(), _INSTRUMENT_PROFILES["NIFTY"])
            _step_sb  = _prof_sb["strike_step"]
            # Find closest existing position on the add side, go 1 step tighter
            _OP_sb    = re.compile(r"^[A-Z]+?\d{2}[A-Z]{3}\d{2}(\d+)(CE|PE)$")
            _same_sb  = [p for p in _open_legs_sb if _add_sb in p.symbol]
            if _same_sb:
                _step_counts = []
                for _ep in _same_sb:
                    _m_sb = _OP_sb.match(_ep.symbol.upper())
                    if _m_sb:
                        _dist_sb = abs(int(_m_sb.group(1)) - _spot_sb)
                        _step_counts.append(round(_dist_sb / _step_sb))
                _new_steps_sb = max(1, min(_step_counts) - 1) if _step_counts else rebalance_otm_steps
            else:
                _new_steps_sb = rebalance_otm_steps
            _otm_sb = (_new_steps_sb * _step_sb) / _spot_sb
            _qty_sb = 1 * _prof_sb["lot"]
            _be_ref = _be_dn_sb if _breach_side == "down" else _be_up_sb
            print(f"  [RULE] SUSTAINED_BE_BREACH: spot {_spot_sb:.0f} outside "
                  f"BE {_be_ref:.0f} for {bars_beyond_be} bars. "
                  f"Adding {_add_sb} @ {_new_steps_sb} steps ({_otm_sb*100:.1f}% OTM)")
            return Decision(
                action="ADD_POSITION", instrument=_add_sb,
                quantity=_qty_sb, direction="SELL", target_otm_pct=_otm_sb,
                reasoning=(
                    f"SUSTAINED_BE_BREACH: spot {_spot_sb:.0f} has been outside "
                    f"BE {_be_ref:.0f} for {bars_beyond_be} bars ({bars_beyond_be*5}min). "
                    f"Adding {_add_sb} at {_new_steps_sb} steps ({_otm_sb*100:.1f}% OTM) "
                    f"to shift profit zone toward current spot."
                ),
                urgency="high", next_review="5min",
            )

    # ── TRAILING PROFIT LOCK ────────────────────────────────────────────────────
    # Activates once session P&L peaks above Rs.3000.
    # When P&L then drops 50%+ from that peak, add 1L on the safe side of the drift
    # (spot up → add PE; spot down → add CE) to shift the profit center back.
    _TRAIL_FLOOR = 3000
    if (peak_pnl >= _TRAIL_FLOOR
            and ctx.current_pnl < peak_pnl * 0.50
            and ctx.current_pnl >= 0            # only protect profits; LOSS MODE handles negatives
            and goal.strategy_type == 'options'
            and len(ctx.positions) >= 2):
        _ectr_t      = getattr(ctx, 'entry_spot', ctx.underlying_price) or ctx.underlying_price
        _drift_t     = ctx.underlying_price - _ectr_t
        _prof_t      = _INSTRUMENT_PROFILES.get(goal.underlying.upper(), _INSTRUMENT_PROFILES['NIFTY'])
        _recent_t    = (ctx.underlying_price - prev_spot) if prev_spot is not None else 0.0
        _signal_t    = _drift_t if abs(_drift_t) > _prof_t['strike_step'] else _recent_t
        _add_t       = 'PE' if _signal_t >= 0 else 'CE'   # moving up → add PE; down → add CE
        # Apply R3 floor so we never add within 0.5% OTM (would immediately trigger R3)
        _r3_floor_t  = int(CRITICAL_OTM_PCT * ctx.underlying_price / _prof_t['strike_step']) + 2
        if rebalance_otm_steps < _r3_floor_t:
            print(f'  [TRAIL] Cannot add — OTM steps depleted ({rebalance_otm_steps} steps < R3 floor {_r3_floor_t} steps). Handing off to LOSS MODE.')
        else:
            _safe_steps_t = rebalance_otm_steps
            _otm_t   = (_safe_steps_t * _prof_t['strike_step']) / ctx.underlying_price
            _qty_t   = 1 * _prof_t['lot']
            print(f'  [TRAIL] P&L ₹{ctx.current_pnl:.0f} fell 50%+ from peak ₹{peak_pnl:.0f}; '
                  f'drift {_drift_t:+.0f}pt → add {_add_t} @ {_otm_t*100:.1f}% OTM ({_safe_steps_t} steps)')
            return Decision(
                action='ADD_POSITION', instrument=_add_t,
                quantity=_qty_t, direction='SELL', target_otm_pct=_otm_t,
                reasoning=(
                    f'TRAIL_PROTECT: P&L ₹{ctx.current_pnl:,.0f} dropped 50%+ from peak '
                    f'₹{peak_pnl:,.0f}. Spot drifted {_drift_t:+.0f}pt from entry. '
                    f'Adding {_add_t} at {_safe_steps_t} steps ({_otm_t*100:.1f}% OTM) '
                    f'to re-center profit zone.'
                ),
                urgency='high', next_review='5min',
            )

    # ── LOSS MODE: BE-breach recovery ────────────────────────────────────────────
    # When session P&L < threshold, check if spot is outside a breakeven:
    #   Outside BE  -> add safe side (CE if below lower BE, PE if above upper BE)
    #   Inside BEs  -> hold; theta working; no add needed
    #   Re-fires    -> only when spot exits BE zone again after being inside
    _loss_threshold = max(goal.max_loss * 0.05, -500)
    _in_loss = ctx.current_pnl < _loss_threshold
    if _in_loss and goal.strategy_type == 'options' and len(ctx.positions) >= 1:
        _pm_l    = _compute_payoff_metrics(ctx.positions, ctx.underlying_price, getattr(ctx, "bar_dt", None), _realized_pnl_offset)
        _spot_l  = ctx.underlying_price
        _be_dn_l = _pm_l.get('breakeven_down')
        _be_up_l = _pm_l.get('breakeven_up')
        _prof_l  = _INSTRUMENT_PROFILES.get(goal.underlying.upper(), _INSTRUMENT_PROFILES['NIFTY'])
        _lot_l   = _prof_l['lot']
        _step_l  = _prof_l['strike_step']
        _qty_l   = 1 * _lot_l
        _below_be_l   = _be_dn_l is not None and _spot_l < _be_dn_l
        _above_be_l   = _be_up_l is not None and _spot_l > _be_up_l
        _outside_be_l = _below_be_l or _above_be_l
        _add_inst = None
        _trigger  = None
        if _outside_be_l and loss_otm_steps >= 1:
            if last_loss_add_type is None:
                # First recovery add this loss episode
                _add_inst = 'CE' if _below_be_l else 'PE'
                _trigger  = 'LOSS_RECOVERY_ENTER'
            elif prev_spot is not None and prev_in_loss:
                # Re-fire only if spot was inside BEs last bar and is now back outside
                _prev_below = _be_dn_l is not None and prev_spot < _be_dn_l
                _prev_above = _be_up_l is not None and prev_spot > _be_up_l
                if not (_prev_below or _prev_above):
                    _add_inst = 'CE' if _below_be_l else 'PE'
                    _trigger  = 'LOSS_RECOVERY_CROSS'
        if _add_inst:
            _otm_pct_l = (loss_otm_steps * _step_l) / _spot_l
            _be_ref_l  = _be_dn_l if _below_be_l else _be_up_l
            print(f'  [RULE] {_trigger}: adding {_add_inst} @ {loss_otm_steps} steps OTM ({_otm_pct_l*100:.1f}%)  spot={_spot_l:.0f}  BE={_be_ref_l:.0f}  pnl={ctx.current_pnl:.0f}')
            return Decision(
                action='ADD_POSITION', instrument=_add_inst,
                quantity=_qty_l, direction='SELL',
                target_otm_pct=_otm_pct_l,
                reasoning=(
                    f'{_trigger}: P&L Rs.{ctx.current_pnl:,.0f}, spot {_spot_l:.0f} outside BE {_be_ref_l:.0f}. '
                    f'Adding {_add_inst} at {loss_otm_steps} steps ({_otm_pct_l*100:.1f}% OTM) '
                    f'to widen range and recover.'
                ),
                urgency='high', next_review='5min',
            )
        # No BE-breach add fired. If no BEs exist at all (one-sided position +
        # realized losses too large to break even), add the missing leg to rebuild
        # the strangle — gives theta on both sides and may create new BEs.
        if _be_dn_l is None and _be_up_l is None and loss_otm_steps >= 1:
            _has_ce_r = any("CE" in p.symbol.upper() and p.qty != 0
                            for p in ctx.positions)
            _has_pe_r = any("PE" in p.symbol.upper() and p.qty != 0
                            for p in ctx.positions)
            _rebuild = None
            if _has_pe_r and not _has_ce_r:
                _rebuild = "CE"
            elif _has_ce_r and not _has_pe_r:
                _rebuild = "PE"
            # Only fire if we haven't already added this leg in this loss episode
            if _rebuild and last_loss_add_type != _rebuild:
                _otm_pct_r = (loss_otm_steps * _step_l) / _spot_l
                _side_desc = "PE-only" if _has_pe_r else "CE-only"
                print(f'  [RULE] LOSS_RECOVERY_REBUILD: {_side_desc} position, no BEs exist; '
                      f'adding {_rebuild} @ {loss_otm_steps} steps ({_otm_pct_r*100:.1f}% OTM)')
                return Decision(
                    action='ADD_POSITION', instrument=_rebuild,
                    quantity=_qty_l, direction='SELL',
                    target_otm_pct=_otm_pct_r,
                    reasoning=(
                        f'LOSS_RECOVERY_REBUILD: P&L Rs.{ctx.current_pnl:,.0f}, '
                        f'position is {_side_desc} with no breakevens possible. '
                        f'Adding {_rebuild} at {loss_otm_steps} steps ({_otm_pct_r*100:.1f}% OTM) '
                        f'to rebuild strangle and create recovery opportunity.'
                    ),
                    urgency='high', next_review='5min',
                )
        return None  # in loss mode but inside BEs — hold; no BE_RECENTER

    # ── PROFIT MODE: BE-proximity re-center ──────────────────────────────────────
    # BE-proximity re-center: when spot approaches a breakeven, add to the SAFE (opposite) leg.
    # OTM for re-centering adds is CLOSER than initial entry (can go down to 1.0%):
    #   drift < 150pts  -> 1.5% OTM, 2 lots  (~64pt center shift)
    #   drift 150-250   -> 1.5% OTM, 3 lots  (~84pt shift)
    #   drift > 250     -> 1.0% OTM, 3 lots  (~112pt shift)
    # The closer OTM is intentional: re-centering needs aggressive strike placement.
    # D: Opening freeze — block BE_RECENTER for first 15 min of session (09:15-09:30 IST)
    _now_open = getattr(ctx, 'bar_dt', None) or datetime.now(IST)
    if _now_open.hour == 9 and _now_open.minute < 30:
        print(f'  [D] BE-RECENTER skipped: opening freeze until 09:30 IST ({_now_open.strftime("%H:%M")} bar)')
        return None
    # BE_RECENTER cooldown: skip if an ADD was made within the last 3 bars
    _cooldown_bars = 3
    _bars_since_add = getattr(ctx, 'bars_since_last_add', 999)
    if _bars_since_add < _cooldown_bars:
        print(f'  [COOLDOWN] BE-RECENTER skipped ({_bars_since_add} bar(s) since last add, need {_cooldown_bars})')
    elif goal.strategy_type == 'options' and len(ctx.positions) >= 2:
        _pm2      = _compute_payoff_metrics(ctx.positions, ctx.underlying_price, getattr(ctx, "bar_dt", None), _realized_pnl_offset)
        _be_up2   = _pm2.get('breakeven_up')
        _be_down2 = _pm2.get('breakeven_down')
        _cur_ctr  = _pm2.get('max_profit_spot')
        if _be_up2 and _be_down2 and _be_up2 > _be_down2 and _cur_ctr:
            _range2   = _be_up2 - _be_down2
            _spot2    = ctx.underlying_price
            _dist_up2 = _be_up2 - _spot2
            _dist_dn2 = _spot2 - _be_down2
            _drift    = abs(_spot2 - _cur_ctr)
            _pnl_ok   = ctx.current_pnl > goal.max_loss * 0.5
            _dte_ok   = dte is None or (isinstance(dte, (int, float)) and dte > 2)
            _prof     = _INSTRUMENT_PROFILES.get(goal.underlying.upper(), _INSTRUMENT_PROFILES['NIFTY'])
            _lot2     = _prof['lot']
            # Drift-adaptive OTM and lot sizing for re-centering
            if _drift < 150:
                _add_lots, _otm_pct, _shift_est = 2, 0.015, 64
            elif _drift < 250:
                _add_lots, _otm_pct, _shift_est = 3, 0.015, 84
            else:
                _add_lots, _otm_pct, _shift_est = 3, 0.010, 112
            _add_qty = _add_lots * _lot2

            _entry_ctr_bc = getattr(ctx, 'entry_spot', 0) or 0
            # B: threshold tightened 30%→15% | C: require >50pt move from original entry center
            # Spot approaching upper BE → add PE
            if _pnl_ok and _dte_ok and _dist_up2 < _range2 * 0.15 and _spot2 > _cur_ctr:
                if _entry_ctr_bc > 0 and abs(_spot2 - _entry_ctr_bc) < 50:
                    print(f'  [C] BE-RECENTER up skipped: only {abs(_spot2-_entry_ctr_bc):.0f}pt from entry center {_entry_ctr_bc:.0f} (need >50pt)')
                else:
                    _pe_legs = [p for p in ctx.positions
                                if p.qty < 0 and 'PE' in p.symbol and p.otm_pct and p.otm_pct > _otm_pct * 0.5]
                    if _pe_legs:
                        _pct_u = int(_dist_up2 / _range2 * 100)
                        _new_ctr_up = _cur_ctr + _shift_est
                        print(f'  [RULE] BE-RECENTER up: drift={_drift:.0f}pts, {_add_lots}L PE @ {_otm_pct*100:.1f}% OTM (est +{_shift_est}pt shift)')
                        print(f'  [CENTER] {_cur_ctr} → ~{_new_ctr_up}')
                        return Decision(
                            action='ADD_POSITION', instrument='PE',
                            quantity=_add_qty, direction='SELL',
                            target_otm_pct=_otm_pct,
                            reasoning=(
                                f'BE_RECENTER: spot {_spot2:.0f} within {_pct_u}% of BE_up {_be_up2}'
                                f' (center drifted {_drift:.0f}pts up). Profit center: {_cur_ctr} → ~{_new_ctr_up}.'
                                f' Adding {_add_lots}L PE at {_otm_pct*100:.1f}% OTM.'
                            ),
                            urgency='medium', next_review='5min',
                        )

            # Spot approaching lower BE → add CE
            if _pnl_ok and _dte_ok and _dist_dn2 < _range2 * 0.15 and _spot2 < _cur_ctr:
                if _entry_ctr_bc > 0 and abs(_spot2 - _entry_ctr_bc) < 50:
                    print(f'  [C] BE-RECENTER down skipped: only {abs(_spot2-_entry_ctr_bc):.0f}pt from entry center {_entry_ctr_bc:.0f} (need >50pt)')
                else:
                    _ce_legs = [p for p in ctx.positions
                                if p.qty < 0 and 'CE' in p.symbol and p.otm_pct and p.otm_pct > _otm_pct * 0.5]
                    if _ce_legs:
                        _pct_d = int(_dist_dn2 / _range2 * 100)
                        _new_ctr_dn = _cur_ctr - _shift_est
                        print(f'  [RULE] BE-RECENTER down: drift={_drift:.0f}pts, {_add_lots}L CE @ {_otm_pct*100:.1f}% OTM (est +{_shift_est}pt shift)')
                        print(f'  [CENTER] {_cur_ctr} → ~{_new_ctr_dn}')
                        return Decision(
                            action='ADD_POSITION', instrument='CE',
                            quantity=_add_qty, direction='SELL',
                            target_otm_pct=_otm_pct,
                            reasoning=(
                                f'BE_RECENTER: spot {_spot2:.0f} within {_pct_d}% of BE_down {_be_down2}'
                                f' (center drifted {_drift:.0f}pts down). Profit center: {_cur_ctr} → ~{_new_ctr_dn}.'
                                f' Adding {_add_lots}L CE at {_otm_pct*100:.1f}% OTM.'
                            ),
                            urgency='medium', next_review='5min',
                        )

    return None   # no hard rule triggered → let LLM decide


# ── Build danger summary for LLM context ─────────────────────────────────────

def _danger_summary(positions: list[PositionSnapshot]) -> list[dict]:
    """
    Constructs a flagged list of risky positions for the LLM prompt.
    Each dict includes the symbol, risk level, and why it's flagged.
    """
    flags = []
    for p in positions:
        if p.qty >= 0:
            continue  # only short legs can be threatened
        flag = {}
        if p.otm_pct is not None:
            if p.otm_pct < DANGER_OTM_PCT:
                flag["otm_risk"] = f"DANGER: {p.otm_pct*100:.2f}% OTM < 1.0%"
            elif p.otm_pct < WARNING_OTM_PCT:
                flag["otm_risk"] = f"WARNING: {p.otm_pct*100:.2f}% OTM < 1.5%"
        if p.premium_ratio is not None and p.premium_ratio > DOUBLE_RULE_RATIO:
            flag["premium_risk"] = f"DOUBLED: premium ratio {p.premium_ratio:.2f}x (sold at {p.avg_price:.2f}, now {p.ltp:.2f})"
        if flag:
            flags.append({"symbol": p.symbol, "pnl": p.pnl, **flag})
    return flags


# ── Instrument profiles ───────────────────────────────────────────────────────

_INSTRUMENT_PROFILES = {
    "NIFTY": {
        "lot": 65, "strike_step": 50,
        "min_premium_inr": 20, "min_viable_premium_inr": 10,
        "premium_target_range": "Rs.25-75",
        "typical_daily_range_pct": "0.35-0.65%",
        "volatile_daily_range_pct": "1.1-1.7%",
    },
    "BANKNIFTY": {
        "lot": 15, "strike_step": 100,
        "min_premium_inr": 20, "min_viable_premium_inr": 10,
        "premium_target_range": "Rs.25-75",
        "typical_daily_range_pct": "0.4-0.8%",
        "volatile_daily_range_pct": "1.2-2.0%",
    },
    "GOLDM": {
        "lot": 1, "strike_step": 500,
        "min_premium_inr": 100, "min_viable_premium_inr": 50,
        "premium_target_range": "Rs.200-800",
        "typical_daily_range_pct": "0.3-0.6%",
        "volatile_daily_range_pct": "1.0-2.0%",
    },
}

def _instrument_profile(underlying: str) -> dict:
    return _INSTRUMENT_PROFILES.get(underlying.upper(), _INSTRUMENT_PROFILES["NIFTY"])


def _compute_payoff_metrics(positions: list, spot: float, now: object = None, realized_pnl_offset: float = 0.0) -> dict:
    """Returns max_profit_spot, pct_spot_from_center, breakeven_down/up for LLM."""
    try:
        import re as _re
        from opengreeks.black_scholes import black_scholes as _bs, implied_volatility as _iv_fn
        _OP = _re.compile(r"^([A-Z]+?)(\d{2})([A-Z]{3})(\d{2})(\d+)(CE|PE)$")
        R = 0.065
        # Use historical bar time for replay (so T is correct); fall back to real clock
        _now = (now.replace(tzinfo=None) if now is not None else datetime.now(IST).replace(tzinfo=None))
        legs = []
        for p in positions:
            if p.qty == 0 or p.ltp <= 0 or p.avg_price <= 0:
                continue
            m = _OP.match(p.symbol.upper())
            if not m:
                continue
            K    = int(m.group(5))
            flag = "c" if m.group(6) == "CE" else "p"
            try:
                # Try YY-MMM-DD (replay) then DD-MMM-YY (live) — pick whichever gives future date
                _raw = f"{m.group(2)}{m.group(3)}{m.group(4)}"
                try:
                    exp_dt = datetime.strptime(_raw, "%y%b%d")
                except ValueError:
                    exp_dt = datetime.strptime(_raw, "%d%b%y")
                if (exp_dt - _now).days < -30:  # parsed into the past, try other format
                    try:
                        exp_dt = datetime.strptime(_raw, "%d%b%y")
                    except ValueError:
                        pass
            except ValueError:
                continue
            T = max((exp_dt - _now).total_seconds() / (365.25 * 24 * 3600), 1e-6)
            iv = 0.20
            try:
                iv = float(_iv_fn(p.ltp, spot, K, T, R, flag))
                iv = max(0.01, min(iv, 5.0))
            except Exception:
                pass
            legs.append({"qty": p.qty, "avg": p.avg_price, "K": K, "flag": flag, "T": T, "iv": iv})

        if not legs:
            return {}

        N  = 60
        _all_ks = [lg["K"] for lg in legs]
        lo = min(spot * 0.93, min(_all_ks) * 0.95) if _all_ks else spot * 0.93
        hi = max(spot * 1.07, max(_all_ks) * 1.05) if _all_ks else spot * 1.07
        spots_ = [lo + (hi - lo) * i / (N - 1) for i in range(N)]
        pnls = []
        for S in spots_:
            tp = 0.0
            for lg in legs:
                try:
                    theo = max(float(_bs(lg["flag"], S, lg["K"], lg["T"], R, lg["iv"])), 0.0)
                except Exception:
                    theo = max(S-lg["K"],0) if lg["flag"]=="c" else max(lg["K"]-S,0)
                if lg["qty"] < 0:
                    tp += (lg["avg"] - theo) * abs(lg["qty"])
                else:
                    tp += (theo - lg["avg"]) * abs(lg["qty"])
            pnls.append(tp + realized_pnl_offset)

        max_idx         = pnls.index(max(pnls))
        max_profit_spot = round(spots_[max_idx])
        pct_from_center = round((spot - max_profit_spot) / max_profit_spot * 100, 3)

        # Collect all sign-change crossings, sort, take outermost pair.
        # NOT split by "above/below spot" — when spot has already crossed a BE
        # the crossing sits below spot and the old guard missed it, giving be_up=None.
        _crossings = []
        for i in range(len(pnls) - 1):
            a, b = pnls[i], pnls[i + 1]
            if (a <= 0 <= b) or (a >= 0 >= b):
                be = spots_[i] + (spots_[i+1]-spots_[i]) * (-a/(b-a)) if b != a else spots_[i]
                _crossings.append(round(be))
        _crossings.sort()
        be_down = _crossings[0]  if len(_crossings) >= 1 else None
        be_up   = _crossings[-1] if len(_crossings) >= 2 else None

        return {
            "max_profit_spot":    max_profit_spot,
            "pct_spot_from_center": pct_from_center,
            "breakeven_down":     be_down,
            "breakeven_up":       be_up,
        }
    except Exception:
        return {}



# ── LLM call ─────────────────────────────────────────────────────────────────

def _equity_ctx(goal: Goal, price: float) -> dict:
    """Equity/futures goal fields injected into LLM compact_ctx."""
    if goal.strategy_type not in ("equity", "futures"):
        return {}
    entry     = goal.entry_price or price
    stop      = goal.stop_loss_price
    tgt       = goal.target_price
    direction = goal.direction or "LONG"
    if direction == "LONG":
        dist_stop = round((price - stop)  / price * 100, 2) if stop  else None
        dist_tgt  = round((tgt   - price) / price * 100, 2) if tgt   else None
        pnl_pct   = round((price - entry) / entry * 100, 2) if entry else None
    else:
        dist_stop = round((stop  - price) / price * 100, 2) if stop  else None
        dist_tgt  = round((price - tgt)   / price * 100, 2) if tgt   else None
        pnl_pct   = round((entry - price) / entry * 100, 2) if entry else None
    return {
        "direction":          direction,
        "entry_price":        entry,
        "current_price":      price,
        "pnl_pct":            pnl_pct,
        "target_price":       tgt,
        "stop_loss_price":    stop,
        "trailing_stop_pct":  goal.trailing_stop_pct,
        "dist_to_stop_pct":   dist_stop,
        "dist_to_target_pct": dist_tgt,
        "qty":                goal.qty or goal.lots,
    }


def call_llm(goal: Goal, ctx: ContextSnapshot, client: OpenAI) -> Decision:
    """Sends goal + context to GPT-4o, returns parsed Decision."""
    dte = ctx.positions[0].dte if ctx.positions and ctx.positions[0].dte is not None else "?"

    compact_ctx = {
        "timestamp": ctx.timestamp_ist,
        "pnl_inr": ctx.current_pnl,
        "net_delta": round(ctx.net_delta, 4),
        "net_theta_per_day": round(ctx.net_theta, 2),
        "net_vega": round(ctx.net_vega, 2),
        "underlying_price": ctx.underlying_price,
        "move_from_open_pts": ctx.underlying_move_pts,
        "move_from_open_pct": ctx.underlying_move_pct,
        "vix": ctx.vix_now,
        "pcr": ctx.pcr_now,
        "pcr_trend": ctx.pcr_trend,
        "dte": dte,
        "time_to_expiry_hours": ctx.time_to_expiry_hours,
        "oi_shifts": ctx.oi_shift_summary,
        "morning_signal": ctx.morning_brief.get("signal") if ctx.morning_brief else None,
        # Per-leg detail including risk fields
        "open_legs": [
            {
                "symbol":        p.symbol,
                "qty":           p.qty,
                "avg_price":     p.avg_price,
                "ltp":           p.ltp,
                "pnl":           p.pnl,
                "otm_pct":       f"{p.otm_pct*100:.2f}%" if p.otm_pct is not None else None,
                "premium_ratio": p.premium_ratio,
                "dte":           p.dte,
            }
            for p in ctx.positions
        ],
        # Pre-computed danger flags — LLM doesn't need to re-derive these
        "danger_flags": _danger_summary(ctx.positions),
        # Intraday extremes — used for B1/B2 % move checks
        "intraday_high": ctx.intraday_high,
        "intraday_low":  ctx.intraday_low,
        "pct_from_high": round((ctx.underlying_price - ctx.intraday_high) / ctx.intraday_high * 100, 3)
                         if ctx.intraday_high else None,
        "pct_from_low":  round((ctx.underlying_price - ctx.intraday_low) / ctx.intraday_low * 100, 3)
                         if ctx.intraday_low else None,
        # Per-instrument parameters — used by A3/A5/B4/C4/F rules
        "instrument_profile": _instrument_profile(goal.underlying),
        # Payoff shape — center of theoretical P&L curve, drift from center, breakevens
        **_compute_payoff_metrics(ctx.positions, ctx.underlying_price, getattr(ctx, "bar_dt", None),
                                  ctx.current_pnl - sum(p.pnl for p in ctx.positions)),
        # Equity/futures goal fields (entry, stop, target, P\&L%)
        **_equity_ctx(goal, ctx.underlying_price),
        # Session memory — running narrative of today's decisions and market story
        **_sm.get_context_block(goal.underlying, goal.strategy_id),
    }

    user_message = (
        f"GOAL:\n{json.dumps(goal.model_dump(), indent=2)}\n\n"
        f"CONTEXT:\n{json.dumps(compact_ctx, indent=2)}\n\n"
        "Based on the above, what is your decision?"
    )

    response = client.chat.completions.create(
        model=LLM_MODEL,
        max_tokens=300,
        timeout=45,
        messages=[
            {"role": "system", "content": (
            EQUITY_SYSTEM_PROMPT if goal.strategy_type == "equity"
            else FUTURES_SYSTEM_PROMPT if goal.strategy_type == "futures"
            else SYSTEM_PROMPT
        )},
            {"role": "user",   "content": user_message},
        ],
    )

    _llm_usage["calls"] += 1
    if response.usage:
        _llm_usage["prompt_tokens"]     += response.usage.prompt_tokens
        _llm_usage["completion_tokens"] += response.usage.completion_tokens
    raw = response.choices[0].message.content.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    data = json.loads(raw)

    # Normalise next_review — LLMs sometimes return bare numbers or other strings
    if data.get("next_review") not in ("1min", "5min", "15min", "30min"):
        data["next_review"] = "1min"

    # Normalise direction — LLMs invent values like UP/DOWN/LONG/ROLL_UP etc.
    # Anything that isn't strictly BUY or SELL is coerced to None (Optional field).
    _dir_map = {
        "BUY": "BUY", "SELL": "SELL",
        "UP": "BUY", "LONG": "BUY", "CLOSE": "BUY", "COVER": "BUY",
        "DOWN": "SELL", "SHORT": "SELL", "OPEN": "SELL",
    }
    raw_dir = data.get("direction")
    data["direction"] = _dir_map.get(str(raw_dir).upper(), None) if raw_dir else None

    return Decision(**data)


# ── Telegram ─────────────────────────────────────────────────────────────────

def _send_telegram(msg: str):
    token   = os.environ.get("TELEGRAM_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return
    try:
        import requests as req
        req.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": msg},
            timeout=5,
        )
    except Exception:
        pass


# ── Mock context for --simulate ───────────────────────────────────────────────

def _mock_context(goal: Goal, cycle: int, entry_price: float) -> ContextSnapshot:
    """Simulated short strangle context that evolves each cycle for testing."""
    import math
    now_ist = datetime.now(IST)
    ts = now_ist.strftime("%H:%M IST")

    move = [0, 80, 50, -120, 200, -180][cycle % 6]
    spot = entry_price + move
    pnl  = [1200, -800, 300, -4500, -1500, 600][cycle % 6]
    vix  = [14.5, 16.2, 15.8, 19.3, 18.3, 17.6][cycle % 6]
    pcr  = [1.05, 0.82, 0.91, 0.68, 0.75, 0.88][cycle % 6]
    pcr_t = ["flat","falling","flat","falling","falling","flat"][cycle % 6]

    expiry_dt = datetime.strptime(goal.expiry, "%Y-%m-%d").replace(hour=15, minute=30, tzinfo=IST)
    tte = max((expiry_dt - now_ist).total_seconds() / 3600, 0)
    dte_val = max((expiry_dt.replace(tzinfo=None) - now_ist.replace(tzinfo=None)).days, 0)

    ce_strike = int(entry_price + 500)
    pe_strike = int(entry_price - 500)
    ce_otm = (ce_strike - spot) / spot
    pe_otm = (spot - pe_strike) / spot

    positions = [
        PositionSnapshot(
            symbol=f"{goal.underlying}23JUN26{ce_strike}CE",
            product="MIS", qty=-75, avg_price=45.0,
            ltp=round(45.0 * [1.0,1.2,0.9,2.5,3.0,0.7][cycle % 6], 2),
            pnl=round(pnl * 0.4, 0),
            delta=-0.18, theta=12.0, vega=-45.0,
            otm_pct=round(max(ce_otm, 0), 4),
            premium_ratio=round([1.0,1.2,0.9,2.5,3.0,0.7][cycle % 6], 3),
            dte=dte_val,
        ),
        PositionSnapshot(
            symbol=f"{goal.underlying}23JUN26{pe_strike}PE",
            product="MIS", qty=-75, avg_price=40.0,
            ltp=round(40.0 * [1.0,0.8,1.1,1.8,0.6,1.2][cycle % 6], 2),
            pnl=round(pnl * 0.6, 0),
            delta=0.15, theta=11.0, vega=-42.0,
            otm_pct=round(max(pe_otm, 0), 4),
            premium_ratio=round([1.0,0.8,1.1,1.8,0.6,1.2][cycle % 6], 3),
            dte=dte_val,
        ),
    ]

    return ContextSnapshot(
        timestamp_ist=ts,
        current_pnl=float(pnl),
        net_delta=0.05, net_theta=23.0, net_vega=-87.0,
        underlying_price=float(spot),
        underlying_move_pts=float(move),
        underlying_move_pct=round(move / entry_price * 100, 2),
        vix_now=vix, pcr_now=pcr, pcr_trend=pcr_t,
        time_to_expiry_hours=round(tte, 2),
        positions=positions,
        oi_shift_summary="Simulated OI — no real data",
        morning_brief={"signal": "HOLD", "confidence": 0.6,
                        "rationale": "Simulated session"},
    )


# ── Main loop ─────────────────────────────────────────────────────────────────

def run_loop(
    goal: Goal,
    api_key: str,
    entry_price: float,
    dry_run: bool,
    force: bool = False,
    simulate: bool = False,
    cycle_secs: int = CYCLE_SECONDS,
):
    """1-minute loop. Runs until market closes, hard stop, or exit window."""
    client = OpenAI(
        api_key=os.environ["OPENAI_API_KEY"],
        timeout=httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=5.0),
    )

    print(f"\nPosition Manager started — {goal.underlying}")
    print(f"  Target: Rs.{goal.target_profit:,}  |  Floor: Rs.{goal.max_loss:,}")
    print(f"  Expiry: {goal.expiry}  |  Style: {goal.style}  |  Entry spot: {entry_price}")
    flags = []
    if dry_run:  flags.append("DRY RUN")
    if simulate: flags.append("SIMULATE")
    if force:    flags.append("FORCE")
    if flags:    print(f"  Mode: {' | '.join(flags)}")
    print(f"  Cycle: every {CYCLE_SECONDS}s\n")

    cycle = 0
    session_closed = False  # set True once DTE time exit fires
    _session_init_done  = False
    prev_spot          = None   # previous cycle spot (center-crossing detection)
    prev_in_loss       = False  # was P&L < 0 last cycle
    last_loss_add_type = None   # "CE" or "PE" — last recovery add
    loss_otm_steps     = 5      # starts 5 steps OTM; decrements each add (min 1)
    last_add_time       = None  # A: 15-min live cooldown between adds
    peak_pnl            = 0.0   # trailing: highest P&L seen this session
    rebalance_otm_steps = 5     # OTM steps for REBALANCE/TRAIL adds; decrements each add
    bars_beyond_be      = 0     # consecutive bars spot has been outside a breakeven

    while True:
        now = datetime.now(IST)

        if not _is_market_hours(goal.underlying) and not force:
            print(f"[{now.strftime('%H:%M IST')}] Outside market hours — exiting loop")
            break

        cycle += 1
        print(f"\n{'─'*54}")
        print(f"Cycle {cycle} — {now.strftime('%H:%M IST')}")

        decision: Optional[Decision] = None
        ctx: Optional[ContextSnapshot] = None

        try:
            # ── Step 1: Build context ────────────────────────────────────
            ctx = _mock_context(goal, cycle, entry_price) if simulate \
                  else build_context(goal, api_key, entry_underlying_price=entry_price)

            print(
                f"  P&L: Rs.{ctx.current_pnl:,.0f}  |  "
                f"Spot: {ctx.underlying_price:.0f} ({ctx.underlying_move_pts:+.0f}pts)  |  "
                f"VIX: {ctx.vix_now or 'n/a'}  |  PCR: {ctx.pcr_now or 'n/a'} ({ctx.pcr_trend or ''})"
            )
            # Initialise session memory once per run (after first context snapshot so we have positions)
            if not _session_init_done:
                _sm.init_session(
                    underlying=goal.underlying,
                    entry_spot=entry_price,
                    goal=goal.model_dump(),
                    positions=[
                        {"symbol": p.symbol, "qty": p.qty, "avg_price": p.avg_price}
                        for p in ctx.positions
                    ],
                    strategy_id=goal.strategy_id,
                )
                _session_init_done = True
            # Print per-leg risk summary
            for p in ctx.positions:
                if p.qty < 0:
                    otm_str = f"{p.otm_pct*100:.2f}%" if p.otm_pct is not None else "?"
                    pr_str  = f"{p.premium_ratio:.2f}x" if p.premium_ratio is not None else "?"
                    flag    = ""
                    if p.otm_pct is not None:
                        if p.otm_pct < CRITICAL_OTM_PCT: flag = " !! CRITICAL"
                        elif p.otm_pct < DANGER_OTM_PCT: flag = " ! DANGER"
                        elif p.otm_pct < WARNING_OTM_PCT: flag = " ~ WARNING"
                    if p.premium_ratio is not None and p.premium_ratio > DOUBLE_RULE_RATIO:
                        flag += " DOUBLED"
                    print(f"    {p.symbol:35s}  OTM={otm_str:7}  ratio={pr_str:5}  P&L=Rs.{p.pnl:+,.0f}{flag}")

            # ── Step 2: Portfolio hard stop ──────────────────────────────
            if hard_stop_check(ctx.current_pnl, goal.max_loss, api_key, dry_run):
                # decision is None at this point (set in Steps 3-4) — use hard_stop directly
                _sm.clear_owned_symbols(goal.underlying, goal.strategy_id)
                log_decision(
                    goal=goal.model_dump(),
                    context=ctx.model_dump(mode="json"),
                    decision={"action": "FULL_EXIT", "reasoning": "Portfolio hard stop"},
                    executed=not dry_run,
                    execution_detail="hard_stop",
                    decision_source="rules",
                )
                break

            # ── Step 3: Deterministic behavioural checks ─────────────────
            _LIVE_ADD_GAP = 15 * 60  # A: 15 minutes minimum between BE_RECENTER adds (same as 3-bar replay cooldown)
            _now_a = datetime.now(IST)
            if last_add_time and (_now_a - last_add_time).total_seconds() < _LIVE_ADD_GAP:
                _rem_a = int(_LIVE_ADD_GAP - (_now_a - last_add_time).total_seconds())
                print(f'  [A] ADD cooldown: {_rem_a//60}m {_rem_a%60}s remaining (last add {last_add_time.strftime("%H:%M IST")})')
                decision = Decision(action='HOLD',
                    reasoning=f'[LIVE_COOLDOWN] {_rem_a//60}m {_rem_a%60}s until next add allowed',
                    urgency='low', next_review='5min')
                source = 'rules'
            else:
                decision = behavioral_checks(
                    ctx, goal,
                    prev_spot=prev_spot,
                    last_loss_add_type=last_loss_add_type,
                    loss_otm_steps=loss_otm_steps,
                    prev_in_loss=prev_in_loss,
                    peak_pnl=peak_pnl,
                    rebalance_otm_steps=rebalance_otm_steps,
                    bars_beyond_be=bars_beyond_be,
                )
                source = "rules"
            if decision and decision.action == "FULL_EXIT":
                session_closed = True  # DTE/hard-stop exit — no more entries today

            if decision is None:
                # ── Step 4: LLM judgment (skipped if user toggled LLM off) ──────────
                _llm_flag = Path(f'/home/freed/autotrade/data/llm_disabled_{goal.underlying.lower()}.flag')
                if _llm_flag.exists():
                    print("  LLM disabled (user toggle) — HOLD")
                    decision = Decision(action='HOLD',
                        reasoning='LLM disabled by user toggle',
                        urgency='low', next_review='5min')
                    source = 'rules'
                else:
                    print("  Calling LLM...", end="", flush=True)
                    decision = call_llm(goal, ctx, client)
                    source = "llm"
                # Guard: block LLM exits when actual R2/R3 conditions are NOT met
                if decision.action in ("PARTIAL_EXIT", "FULL_EXIT"):
                    _r2_met = any(getattr(p,"premium_ratio",None) and p.premium_ratio > 2.0
                                  for p in ctx.positions)
                    _r3_met = any(getattr(p,"otm_pct",None) and p.otm_pct < 0.005
                                  for p in ctx.positions)
                    _loss_ok = ctx.current_pnl > goal.max_loss * 0.5
                    if not _r2_met and not _r3_met and _loss_ok:
                        print(f"  [GUARD] LLM {decision.action} blocked: R2/R3 not met "
                              f"(ratios={[round(getattr(p,'premium_ratio',0),2) for p in ctx.positions]}, "
                              f"OTMs={[round(getattr(p,'otm_pct',0)*100,2) for p in ctx.positions]}%)")
                        decision = Decision(action="HOLD",
                            reasoning="[Exit guard] LLM claimed exit but R2 (ratio>2.0) and R3 (OTM<0.5%) conditions are not met.",
                            urgency="low", next_review="5min")
                if decision.action == "ADD_POSITION" and ctx.current_pnl < 0:
                    _pm = _compute_payoff_metrics(ctx.positions, ctx.underlying_price, getattr(ctx, "bar_dt", None), _realized_pnl_offset)
                    _be_u = _pm.get("breakeven_up")
                    _be_d = _pm.get("breakeven_down")
                    _near_be = False
                    if _be_u and _be_d:
                        _rng = _be_u - _be_d
                        _near_be = ((_be_u - ctx.underlying_price) < _rng * 0.30 or
                                    (ctx.underlying_price - _be_d) < _rng * 0.30)
                    if not _near_be:
                        print("  [GUARD] ADD_POSITION blocked: P&L negative, not near BE")
                        decision = Decision(action="HOLD",
                            reasoning="[P&L guard] Net P&L negative and spot not near breakeven.",
                            urgency="low", next_review="5min")

            print(f" [{source}] → {decision.action}")
            print(f"  Reasoning: {decision.reasoning}")
            print(f"  Urgency: {decision.urgency}")

            # ── Step 5: Execute ──────────────────────────────────────────
            executed = False
            exec_detail = "dry_run"
            if not dry_run:
                if goal.strategy_type in ("equity", "futures"):
                    from decision_executor import execute_equity as _exec_eq
                    executed, exec_detail = _exec_eq(decision, goal.underlying, api_key,
                                                    dry_run, f"pm_{goal.strategy_id}")
                else:
                    executed, exec_detail = execute(decision, ctx, api_key, goal.underlying,
                                                   goal.expiry or "", f"pm_{goal.strategy_id}")
                print(f"  Execution: {exec_detail}")
            else:
                print(f"  [DRY RUN] Would execute: {decision.action}")
                if decision.action not in ("HOLD",):
                    print(f"    instrument={decision.instrument}  qty={decision.quantity}  dir={decision.direction}")

            # ── Step 6: Log ──────────────────────────────────────────────
            log_decision(
                goal=goal.model_dump(),
                context=ctx.model_dump(mode="json"),
                decision=decision.model_dump(),
                executed=executed,
                execution_detail=exec_detail,
                decision_source=source,
            )

            # Update session memory narrative
            _sm.append_decision(
                underlying=goal.underlying,
                ctx={"underlying_price": ctx.underlying_price, "pnl_inr": ctx.current_pnl},
                decision=decision.model_dump(),
                executed=executed,
                source=source,
                strategy_id=goal.strategy_id,
            )

            # Clear owned_symbols and stop the loop after a full exit
            if decision.action == "FULL_EXIT":
                if executed:
                    _sm.clear_owned_symbols(goal.underlying, goal.strategy_id)
                # Stop monitoring — no positions left to manage
                _send_telegram(
                    f"[{datetime.now(IST).strftime('%H:%M')}] FULL_EXIT executed | "
                    f"Final P&L Rs.{ctx.current_pnl:,.0f} | Strategy stopped."
                )
                break

            # A: Record timestamp of last add for live cooldown
            if decision.action == 'ADD_POSITION' and executed:
                last_add_time = datetime.now(IST)
            # Tighten OTM on each REBALANCE or TRAIL_PROTECT add
            if decision.action == 'ADD_POSITION' and executed:
                _rsn = getattr(decision, 'reasoning', '') or ''
                if 'REBALANCE' in _rsn or 'TRAIL_PROTECT' in _rsn:
                    rebalance_otm_steps = max(1, rebalance_otm_steps - 1)
                if 'SUSTAINED_BE_BREACH' in _rsn:
                    bars_beyond_be = 0  # reset after add so counter restarts
            # Track peak P&L for trailing
            peak_pnl = max(peak_pnl, ctx.current_pnl)
            # Update loss-mode state after ADD in loss mode
            if decision.action == "ADD_POSITION" and executed and ctx.current_pnl < 0:
                if hasattr(decision, "instrument") and decision.instrument:
                    last_loss_add_type = decision.instrument
                    loss_otm_steps = max(1, loss_otm_steps - 1)
            # Update per-cycle prev-bar state
            prev_spot    = ctx.underlying_price
            _live_threshold = max(goal.max_loss * 0.05, -500)
            prev_in_loss = ctx.current_pnl < _live_threshold
            # Update bars_beyond_be counter (handles 0, 1, or 2 BEs)
            _pm_be_live = _compute_payoff_metrics(ctx.positions, ctx.underlying_price,
                                                   None, ctx.current_pnl - sum(p.pnl for p in ctx.positions))
            _be_dn_live = _pm_be_live.get("breakeven_down")
            _be_up_live = _pm_be_live.get("breakeven_up")
            _spot_live  = ctx.underlying_price
            _outside_be = (
                (_be_dn_live and _spot_live < _be_dn_live) or
                (_be_up_live and _spot_live > _be_up_live)
            )
            if _be_dn_live or _be_up_live:
                bars_beyond_be = (bars_beyond_be + 1) if _outside_be else 0
            else:
                bars_beyond_be = 0
            # Reset loss tracking when P&L returns to profit
            if ctx.current_pnl >= 0:
                last_loss_add_type  = None
                loss_otm_steps      = 5
                rebalance_otm_steps = 5   # reset when P&L recovers
                bars_beyond_be      = 0   # reset BE breach counter on profit

            # Telegram on any non-HOLD
            if decision.action != "HOLD":
                _send_telegram(
                    f"[{now.strftime('%H:%M')}] {decision.action} | "
                    f"P&L Rs.{ctx.current_pnl:,.0f} | {decision.reasoning[:120]}"
                )

        except Exception as e:
            import traceback
            print(f"  ERROR cycle {cycle}: {e}")
            traceback.print_exc(file=sys.stdout)
            sys.stdout.flush()

        # ── Step 7: Sleep 60s ────────────────────────────────────────────
        sleep_secs = cycle_secs
        next_at = datetime.now(IST) + timedelta(seconds=sleep_secs)
        print(f"  Next cycle at {next_at.strftime('%H:%M:%S IST')}")
        time.sleep(sleep_secs)

    print("\nPosition Manager loop ended.")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Goal-directed intraday position manager")
    parser.add_argument("--underlying",       default="NIFTY")
    parser.add_argument("--strategy-id",     default="default", help="Unique strategy ID for order tagging")
    parser.add_argument("--strategy-type",    default="options", choices=["options", "equity", "futures"])
    parser.add_argument("--target",           type=float, required=True, help="Target profit INR")
    parser.add_argument("--max-loss",         type=float, required=True, help="Max loss INR (negative)")
    parser.add_argument("--expiry",           default=None, help="Option expiry YYYY-MM-DD (options only)")
    parser.add_argument("--entry-price",      type=float, required=True)
    parser.add_argument("--strategy",         default="short_strangle")
    parser.add_argument("--style",            default="conservative",
                        choices=["conservative", "moderate", "aggressive"])
    parser.add_argument("--delta-tol",        type=float, default=0.20)
    parser.add_argument("--protect-at",       type=float, default=0.50)
    parser.add_argument("--direction",        default=None, choices=["LONG", "SHORT"])
    parser.add_argument("--qty",              type=int,   default=None, help="Equity total qty")
    parser.add_argument("--lots",             type=int,   default=None, help="Futures lots")
    parser.add_argument("--target-price",     type=float, default=None)
    parser.add_argument("--stop-loss-price",  type=float, default=None)
    parser.add_argument("--trailing-stop-pct",type=float, default=None)
    parser.add_argument("--morning-brief",    help="Path to today's morning_brief JSON")
    parser.add_argument("--cycle-secs",       type=int,   default=CYCLE_SECONDS)
    parser.add_argument("--dry-run",          action="store_true")
    parser.add_argument("--force",            action="store_true")
    parser.add_argument("--simulate",         action="store_true")
    args = parser.parse_args()

    _load_env()

    for k in ("OPENALGO_API_KEY", "OPENAI_API_KEY"):
        if not os.environ.get(k):
            sys.exit(f"ERROR: {k} not set in ~/autotrade/.env")

    goal = Goal(
        strategy_id=args.strategy_id,
        strategy_type=args.strategy_type,
        strategy=args.strategy,
        underlying=args.underlying.upper(),
        target_profit=args.target,
        max_loss=args.max_loss,
        expiry=args.expiry,
        style=args.style,
        delta_tolerance=args.delta_tol,
        protect_at_pct=args.protect_at,
        direction=args.direction,
        qty=args.qty,
        lots=args.lots,
        entry_price=args.entry_price,
        target_price=args.target_price,
        stop_loss_price=args.stop_loss_price,
        trailing_stop_pct=args.trailing_stop_pct,
        morning_brief_path=args.morning_brief,
    )

    run_loop(
        goal=goal,
        api_key=os.environ["OPENALGO_API_KEY"],
        entry_price=args.entry_price,
        dry_run=args.dry_run,
        force=args.force,
        simulate=args.simulate,
        cycle_secs=args.cycle_secs,
    )


if __name__ == "__main__":
    main()
