"""
goal_schema.py — Pydantic models for Stage 7 goal-directed intraday agent.

Defines: Goal (what we're trying to achieve), ContextSnapshot (live market state
every 15 min), Decision (what Claude outputs), PositionSnapshot (per-leg state).
"""
from __future__ import annotations

from typing import Optional, Literal
from pydantic import BaseModel, Field


class Goal(BaseModel):
    """Trading session goal. Set once at session start, read by agent each cycle."""

    strategy_id:   str = "default"  # unique ID — used to tag orders + filter positions
    strategy_type: Literal["options", "equity", "futures"] = "options"
    strategy: str = "short_strangle"
    underlying: str = "BANKNIFTY"

    target_profit: float = Field(..., description="INR target (positive)")
    max_loss: float = Field(..., description="INR hard floor (negative, e.g. -6000)")

    delta_tolerance: float = Field(0.20, description="Net delta before hedge considered (options only)")
    protect_at_pct: float = Field(0.50, description="Lock profits at this fraction of target")

    expiry: Optional[str] = Field(None, description="Option expiry YYYY-MM-DD (options only)")
    style: Literal["conservative", "moderate", "aggressive"] = "conservative"

    # Equity / Futures parameters
    direction: Optional[Literal["LONG", "SHORT"]] = None
    qty: Optional[int] = None                      # total quantity (equity)
    lots: Optional[int] = None                     # lot count (futures)
    entry_price: Optional[float] = None            # reference entry price
    target_price: Optional[float] = None           # absolute INR price target (equity/futures)
    stop_loss_price: Optional[float] = None        # absolute stop price
    trailing_stop_pct: Optional[float] = None      # trailing stop fraction (0.02 = 2%)

    morning_brief_path: Optional[str] = None


class PositionSnapshot(BaseModel):
    """Single position leg state."""

    symbol: str
    product: str            # MIS / NRML
    qty: int                # positive = long, negative = short
    avg_price: float
    ltp: float
    pnl: float

    # Greeks (best-effort via Black-Scholes; None if symbol can't be parsed)
    delta: Optional[float] = None
    theta: Optional[float] = None
    vega: Optional[float] = None

    # Risk fields — computed by context_builder each cycle
    otm_pct: Optional[float] = None       # fraction OTM vs current spot (0.015 = 1.5%)
    premium_ratio: Optional[float] = None  # ltp / avg_price — >2.0 means premium doubled
    dte: Optional[int] = None              # integer calendar days until expiry


class ContextSnapshot(BaseModel):
    """Full market + position state snapshot built every 15 minutes."""

    timestamp_ist: str              # "09:30 IST"
    current_pnl: float              # net realised + unrealised INR
    net_delta: float                # sum of (delta × qty × lot_size) across legs
    net_theta: float                # INR/day
    net_vega: float

    underlying_price: float         # BANKNIFTY/NIFTY spot
    underlying_move_pts: float      # move in points since position entered
    underlying_move_pct: float      # % move

    vix_now: Optional[float] = None
    pcr_now: Optional[float] = None         # NSE option chain PCR
    pcr_trend: Optional[str] = None         # "rising" / "falling" / "flat"

    time_to_expiry_hours: float
    positions: list[PositionSnapshot]

    oi_shift_summary: Optional[str] = None  # human-readable OI change string
    morning_brief: Optional[dict] = None    # from TradingAgents 6am run

    intraday_high: Optional[float] = None   # today's running spot high
    intraday_low:  Optional[float] = None   # today's running spot low
    bar_dt:        Optional[object] = None  # historical bar datetime for replay (used for correct TTE in BS)
    bars_since_last_add: int = 999          # bars elapsed since last ADD_POSITION (999=never); used for cooldown


class Decision(BaseModel):
    """Structured output from Claude intraday agent."""

    action: Literal[
        "HOLD",           # do nothing this cycle
        "HEDGE_DELTA",    # buy/sell futures to neutralise delta
        "SHIFT_STRIKE",   # roll one leg to a different strike
        "ADD_POSITION",   # add a new short option leg or equity/futures position
        "PARTIAL_EXIT",   # close one or more legs
        "FULL_EXIT",      # close all positions immediately
        "MODIFY_STOP",    # adjust stop loss price (equity/futures)
    ]

    # Required for all actions except HOLD
    instrument: Optional[str] = None   # e.g. "BANKNIFTY-FUT" or option symbol
    quantity: Optional[int] = None
    direction: Optional[Literal["BUY", "SELL"]] = None
    price_type: Literal["MARKET", "LIMIT"] = "MARKET"
    price: Optional[float] = None      # only for LIMIT orders
    new_stop_price: Optional[float] = None  # for MODIFY_STOP action
    target_otm_pct: Optional[float] = None  # for ADD_POSITION re-centering: bypass DTE default OTM

    reasoning: str = ""               # Claude explains WHY in plain English
    urgency: Literal["low", "medium", "high"] = "medium"
    next_review: Literal["1min", "5min", "15min", "30min"] = "1min"
