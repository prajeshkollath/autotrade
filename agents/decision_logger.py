"""
decision_logger.py — Logs every agent decision + full context to JSONL.

Each line is one decision record. Post-market reflection agent reads this file
to evaluate what was decided vs what happened (Stage 8).

Output: ~/autotrade/data/decision_logs/YYYY-MM-DD-{UNDERLYING}.jsonl
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

IST = timezone(timedelta(hours=5, minutes=30))
LOG_DIR = Path("/home/freed/autotrade/data/decision_logs")


def log_decision(
    goal: dict,
    context: dict,
    decision: dict,
    executed: bool,
    execution_detail: Optional[str] = None,
    decision_source: Optional[str] = None,
):
    """
    Appends one JSONL record. Called after every agent decision, whether executed or not.

    executed: True if decision_executor sent the order
    execution_detail: order ID or error string
    decision_source: "llm" or "rules"
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    today    = datetime.now(IST).strftime("%Y-%m-%d")
    sym      = str(goal.get("underlying", "UNKNOWN")).upper()
    log_path = LOG_DIR / f"{today}-{sym}.jsonl"

    record = {
        "ts": datetime.now(IST).isoformat(),
        "goal": goal,
        "context_summary": {
            "timestamp": context.get("timestamp_ist"),
            "pnl": context.get("current_pnl"),
            "net_delta": context.get("net_delta"),
            "underlying_price": context.get("underlying_price"),
            "underlying_move_pts": context.get("underlying_move_pts"),
            "vix": context.get("vix_now"),
            "pcr": context.get("pcr_now"),
            "pcr_trend": context.get("pcr_trend"),
            "tte_hours": context.get("time_to_expiry_hours"),
            "oi_shift": context.get("oi_shift_summary"),
        },
        "decision": decision,
        "decision_source": decision_source,
        "executed": executed,
        "execution_detail": execution_detail,
    }

    with open(log_path, "a") as f:
        f.write(json.dumps(record) + "\n")
