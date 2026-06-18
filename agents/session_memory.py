"""
session_memory.py — Per-strategy running narrative + owned-position tracker.

One file per strategy per day:
  data/session_memory/YYYY-MM-DD-{UNDERLYING}-{STRATEGY_ID}.json

Structure:
  header            — written once at session start
  chapters          — compressed summaries of older decisions
  recent_decisions  — last N decisions in full detail
  owned_symbols     — symbols this strategy has ever opened (for context isolation)
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

IST           = timezone(timedelta(hours=5, minutes=30))
MEMORY_DIR    = Path("/home/freed/autotrade/data/session_memory")
CHAPTER_EVERY = 6
MAX_RECENT    = 4


def _fpath(underlying: str, strategy_id: str = "default") -> Path:
    date = datetime.now(IST).strftime("%Y-%m-%d")
    sid  = strategy_id.replace("pm_", "").strip() or "default"
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    return MEMORY_DIR / f"{date}-{underlying.upper()}-{sid}.json"


def _load(underlying: str, strategy_id: str = "default") -> dict:
    p = _fpath(underlying, strategy_id)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            return {}
    # Cross-date fallback: strategy sessions from previous days (replays, overnight sessions)
    for f in sorted(MEMORY_DIR.glob(f"*-{underlying.upper()}-{strategy_id}.json"), reverse=True):
        try:
            return json.loads(f.read_text())
        except Exception:
            pass
    # Legacy fallback: old files without strategy_id suffix
    date   = datetime.now(IST).strftime("%Y-%m-%d")
    legacy = MEMORY_DIR / f"{date}-{underlying.upper()}.json"
    if legacy.exists():
        try:
            data = json.loads(legacy.read_text())
            p.write_text(json.dumps(data, indent=2))  # migrate
            return data
        except Exception:
            return {}
    return {}


def _save(underlying: str, strategy_id: str, data: dict):
    _fpath(underlying, strategy_id).write_text(json.dumps(data, indent=2))


# ── Public API ─────────────────────────────────────────────────────────────────

def init_session(underlying: str, entry_spot: float, goal: dict,
                 positions: list, strategy_id: str = "default"):
    """
    Called once at session start. Preserves existing history on restart.
    positions: list of {symbol, qty, avg_price}.
    Seeds owned_symbols from positions so context filtering works immediately.
    """
    # Always create a fresh session — never carry over data from a previous run today
    _t_sm = datetime.now(IST)
    data = {
        "underlying":     underlying.upper(),
        "strategy_id":    strategy_id,
        "date":           _t_sm.strftime("%Y-%m-%d"),
        "header": {
            "started_at":         _t_sm.strftime("%H:%M IST"),
            "started_at_iso":     _t_sm.isoformat(),
            "entry_spot":         round(entry_spot),
            "strategy":           goal.get("strategy", "short_strangle"),
            "target_profit":      goal.get("target_profit"),
            "max_loss":           goal.get("max_loss"),
            "expiry":             goal.get("expiry"),
            "positions_at_entry": positions,
        },
        "chapters":         [],
        "recent_decisions": [],
        "owned_symbols":    sorted({p["symbol"].upper() for p in positions if p.get("symbol")}),
    }
    _save(underlying, strategy_id, data)


def add_owned_symbol(underlying: str, symbol: str, strategy_id: str = "default") -> None:
    """Record that this strategy opened a position in `symbol`."""
    data = _load(underlying, strategy_id)
    if not data:
        return
    owned = set(data.get("owned_symbols", []))
    sym = symbol.upper()
    if sym not in owned:
        owned.add(sym)
        data["owned_symbols"] = sorted(owned)
        _save(underlying, strategy_id, data)


def remove_owned_symbol(underlying: str, symbol: str, strategy_id: str = "default") -> None:
    """Remove symbol when position fully closed (FULL_EXIT or specific PARTIAL_EXIT)."""
    data = _load(underlying, strategy_id)
    if not data:
        return
    owned = set(data.get("owned_symbols", []))
    sym = symbol.upper()
    if sym in owned:
        owned.discard(sym)
        data["owned_symbols"] = sorted(owned)
        _save(underlying, strategy_id, data)


def clear_owned_symbols(underlying: str, strategy_id: str = "default") -> None:
    """Clear all owned symbols after FULL_EXIT."""
    data = _load(underlying, strategy_id)
    if not data:
        return
    data["owned_symbols"] = []
    _save(underlying, strategy_id, data)


def get_owned_symbols(underlying: str, strategy_id: str = "default") -> list[str]:
    """Return symbols this strategy owns today. Empty = no session yet (show all)."""
    data = _load(underlying, strategy_id)
    return data.get("owned_symbols", [])


def append_decision(underlying: str, ctx: dict, decision: dict,
                    executed: bool, source: str = "llm",
                    strategy_id: str = "default",
                    timestamp=None):
    data = _load(underlying, strategy_id)
    if not data:
        return

    _ts = timestamp if timestamp is not None else datetime.now(IST)
    entry = {
        "time":     _ts.strftime("%H:%M"),
        "spot":     round(ctx.get("underlying_price") or 0),
        "pnl":      round(ctx.get("pnl_inr") or ctx.get("current_pnl") or 0),
        "action":   decision.get("action", "HOLD"),
        "why":      (decision.get("reasoning") or "")[:120].strip(),
        "executed": executed,
        "source":   source,
    }
    if decision.get("action") != "HOLD" and decision.get("instrument"):
        entry["instrument"] = decision["instrument"]

    data.setdefault("recent_decisions", []).append(entry)

    total = len(data["recent_decisions"])
    if total >= CHAPTER_EVERY + MAX_RECENT:
        to_compress = data["recent_decisions"][:CHAPTER_EVERY]
        data["recent_decisions"] = data["recent_decisions"][CHAPTER_EVERY:]
        data.setdefault("chapters", []).append(_compress(to_compress))

    _save(underlying, strategy_id, data)


def get_context_block(underlying: str, strategy_id: str = "default") -> dict:
    """Returns context dict for LLM injection. Empty dict if no session yet."""
    data = _load(underlying, strategy_id)
    if not data:
        return {}
    return {
        "session_header":   data.get("header", {}),
        "session_chapters": data.get("chapters", []),
        "recent_decisions": data.get("recent_decisions", []),
    }


def _compress(decisions: list) -> str:
    if not decisions:
        return ""
    t_start = decisions[0]["time"];  t_end  = decisions[-1]["time"]
    s_start = decisions[0]["spot"];  s_end  = decisions[-1]["spot"]
    p_start = decisions[0]["pnl"];   p_end  = decisions[-1]["pnl"]
    moves   = s_end - s_start
    actions = [d for d in decisions if d.get("action") != "HOLD"]
    holds   = sum(1 for d in decisions if d.get("action") == "HOLD")
    parts   = []
    for a in actions:
        instr = f" ({a['instrument']})" if a.get("instrument") else ""
        parts.append(f"{a['action']}{instr} at {a['time']} spot {a['spot']:,}")
    act_str  = "; ".join(parts) + ". " if parts else ""
    hold_str = f"{holds} HOLD(s). " if holds else ""
    return (
        f"{t_start}–{t_end} IST: "
        f"Spot {s_start:,}→{s_end:,} ({moves:+,}pts). "
        f"{act_str}{hold_str}"
        f"P&L ₹{p_start:,}→₹{p_end:,}."
    )



def update_live_state(underlying: str, strategy_id: str, state: dict) -> None:
    """Write current replay bar state so the dashboard can display it live."""
    data = _load(underlying, strategy_id)
    data["live_state"] = state
    _save(underlying, strategy_id, data)


def get_live_state(underlying: str, strategy_id: str) -> dict:
    """Return the most recently written live bar state, or empty dict if none."""
    return _load(underlying, strategy_id).get("live_state", {})
