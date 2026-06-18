"""
post_market.py — Stage 10: Post-market reflection + agent learning loop.

Runs at 3:45pm IST (after market close) to review the day's trading session.

What it does:
  1. Loads today's morning_brief.json (what we expected at 6am)
  2. Loads today's entry_log.json (what we entered at 9:15am)
  3. Loads today's decision_logs JSONL (every 15-min agent decision)
  4. Computes session stats: P&L path, decision accuracy, hedge frequency
  5. Calls gpt-4o with all the evidence → structured reflection
  6. Saves learning notes → data/learning_notes/YYYY-MM-DD.json
  7. Updates persistent agent_memory.json (cumulative knowledge across sessions)

The persistent memory feeds back into tomorrow's morning brief context,
closing the learning loop: observe → decide → act → reflect → improve.

HOW TO RUN:
  cd ~/autotrade

  # Reflect on today's session
  .venv/bin/python agents/post_market.py

  # Reflect on a specific date
  .venv/bin/python agents/post_market.py --date 2026-06-09

  # Dry run — show prompt + reflection without saving
  .venv/bin/python agents/post_market.py --dry-run

  # View what the agent has learned so far
  .venv/bin/python agents/post_market.py --show-memory

Outputs:
  data/learning_notes/YYYY-MM-DD.json  — today's reflection
  data/agent_memory.json               — cumulative learning (all sessions)

FRAMEWORK EQUIVALENT:
  This is the "reward + replay" step in RL: after the episode ends,
  evaluate what happened, extract patterns, update policy weights.
  Except here the "weights" are natural language rules stored in a JSON
  memory file, which tomorrow's agents read as context.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import httpx
from openai import OpenAI
from pydantic import BaseModel

IST            = timezone(timedelta(hours=5, minutes=30))
BASE           = Path("/home/freed/autotrade")
BRIEFS_DIR     = BASE / "data/morning_briefs"
ENTRY_LOGS_DIR = BASE / "data/entry_logs"
DECISION_DIR   = BASE / "data/decision_logs"
NOTES_DIR      = BASE / "data/learning_notes"
MEMORY_FILE    = BASE / "data/agent_memory.json"

LLM_MODEL = "gpt-4o"


# ---------------------------------------------------------------------------
# Pydantic output schema for structured reflection
# ---------------------------------------------------------------------------

class DecisionGrade(BaseModel):
    cycle: int
    action: str
    reasoning_summary: str
    grade: str          # CORRECT / PREMATURE / LATE / WRONG
    explanation: str


class LearnedRule(BaseModel):
    rule: str
    condition: str      # "when PCR > 1.0 AND morning_bias == bullish"
    action: str         # "delay hedge by 1 cycle"
    confidence: float   # 0.0–1.0
    source: str         # e.g. "premature hedge cost ₹1,200 on 2026-06-09"


class Reflection(BaseModel):
    session_date: str
    final_pnl: float
    max_intraday_pnl: float
    min_intraday_pnl: float

    # Was the morning thesis right?
    thesis_accuracy: str           # CORRECT / PARTIAL / WRONG
    thesis_explanation: str

    # Were entries at the right levels?
    entry_quality: str             # GOOD / ACCEPTABLE / POOR
    entry_explanation: str

    # Grade each decision
    decision_grades: list[DecisionGrade]

    # Net learning from today
    learned_rules: list[LearnedRule]

    # Which signal types proved useful today
    signals_to_boost: list[str]    # e.g. ["PCR > 1.2 = hold longer", "VIX spike"]
    signals_to_reduce: list[str]   # e.g. ["delta hedge on first cross"]

    # One paragraph written as next-morning context for the agent
    tomorrow_context: str


# ---------------------------------------------------------------------------
# Persistent memory schema
# ---------------------------------------------------------------------------

def _empty_memory() -> dict:
    return {
        "last_updated":    "",
        "sessions_analysed": 0,
        "learned_rules":   [],   # list of LearnedRule dicts (most recent first)
        "signal_accuracy": {},   # {"signal_name": {"correct": N, "total": M}}
        "strategy_performance": {},  # {"iron_condor": {"sessions":N,"avg_pnl":X,"win_rate":R}}
    }


def load_memory() -> dict:
    if MEMORY_FILE.exists():
        return json.loads(MEMORY_FILE.read_text())
    return _empty_memory()


def update_memory(memory: dict, reflection: Reflection, strategy: str) -> dict:
    """Merges today's reflection into the cumulative memory."""
    today = reflection.session_date

    # Increment session count
    memory["sessions_analysed"] += 1
    memory["last_updated"] = today

    # Add new learned rules (dedup by rule text)
    existing_rules = {r["rule"] for r in memory["learned_rules"]}
    for rule in reflection.learned_rules:
        if rule.rule not in existing_rules:
            memory["learned_rules"].insert(0, rule.model_dump())
        else:
            # Update confidence on the existing rule
            for r in memory["learned_rules"]:
                if r["rule"] == rule.rule:
                    # Weighted average confidence
                    r["confidence"] = round((r["confidence"] + rule.confidence) / 2, 2)
                    break

    # Keep only the 20 most recent/high-confidence rules to avoid prompt bloat
    memory["learned_rules"] = sorted(
        memory["learned_rules"], key=lambda r: r["confidence"], reverse=True
    )[:20]

    # Update signal accuracy
    for sig in reflection.signals_to_boost:
        entry = memory["signal_accuracy"].setdefault(sig, {"correct": 0, "total": 0})
        entry["correct"] += 1
        entry["total"]   += 1
    for sig in reflection.signals_to_reduce:
        entry = memory["signal_accuracy"].setdefault(sig, {"correct": 0, "total": 0})
        entry["total"] += 1

    # Update strategy performance
    if strategy and strategy != "unknown":
        perf = memory["strategy_performance"].setdefault(strategy, {
            "sessions": 0, "total_pnl": 0.0, "wins": 0,
        })
        perf["sessions"]  += 1
        perf["total_pnl"] += reflection.final_pnl
        if reflection.final_pnl > 0:
            perf["wins"] += 1
        perf["avg_pnl"]   = round(perf["total_pnl"] / perf["sessions"], 0)
        perf["win_rate"]  = round(perf["wins"] / perf["sessions"], 2)

    return memory


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------

def _load_json(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except Exception:
                    pass
    return records


def _load_today_data(date_str: str) -> tuple[Optional[dict], Optional[dict], list[dict]]:
    """Returns (brief, entry_log, decisions)."""
    brief     = _load_json(BRIEFS_DIR     / f"{date_str}.json")
    entry_log = _load_json(ENTRY_LOGS_DIR / f"{date_str}.json")
    decisions = _load_jsonl(DECISION_DIR  / f"{date_str}.jsonl")
    return brief, entry_log, decisions


# ---------------------------------------------------------------------------
# Session stats
# ---------------------------------------------------------------------------

def compute_session_stats(decisions: list[dict]) -> dict:
    """Extracts key session metrics from the decision log."""
    if not decisions:
        return {"pnl_path": [], "final_pnl": 0, "max_pnl": 0, "min_pnl": 0,
                "total_cycles": 0, "action_counts": {}}

    pnl_path = [r.get("context", {}).get("current_pnl", 0) for r in decisions]
    action_counts: dict[str, int] = {}
    for r in decisions:
        action = r.get("decision", {}).get("action", "UNKNOWN")
        action_counts[action] = action_counts.get(action, 0) + 1

    return {
        "pnl_path":     pnl_path,
        "final_pnl":    pnl_path[-1] if pnl_path else 0,
        "max_pnl":      max(pnl_path) if pnl_path else 0,
        "min_pnl":      min(pnl_path) if pnl_path else 0,
        "total_cycles": len(decisions),
        "action_counts": action_counts,
    }


# ---------------------------------------------------------------------------
# Build reflection prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "You are an expert trading coach reviewing a day's options trading session. "
    "You receive the morning thesis, the entry details, and every 15-minute agent decision. "
    "Your job: grade each decision, extract lessons, and write clear rules for future sessions. "
    "Be honest about mistakes — a premature hedge that cost money is worse than a late one that saved money. "
    "Respond with ONLY valid JSON matching the provided schema. No markdown. No commentary outside JSON."
)


def build_prompt(
    date_str: str,
    brief: Optional[dict],
    entry: Optional[dict],
    decisions: list[dict],
    stats: dict,
    memory: dict,
) -> str:
    """Builds the compact reflection prompt for gpt-4o."""

    # Trim decisions to key fields only — full context per cycle is too large
    compact_decisions = []
    for i, d in enumerate(decisions):
        ctx = d.get("context", {})
        dec = d.get("decision", {})
        compact_decisions.append({
            "cycle":     i + 1,
            "time":      ctx.get("timestamp_ist", "")[:16],
            "pnl":       ctx.get("current_pnl", 0),
            "delta":     round(ctx.get("net_delta", 0), 3),
            "spot_move": ctx.get("banknifty_move_from_entry", 0),
            "pcr":       ctx.get("pcr", 0),
            "vix":       ctx.get("vix", 0),
            "action":    dec.get("action", ""),
            "reasoning": dec.get("reasoning", "")[:120],
            "executed":  d.get("executed", False),
        })

    # Existing memory rules to avoid re-learning the same things
    existing_rules = [r["rule"] for r in memory.get("learned_rules", [])[:5]]

    prompt = {
        "session_date": date_str,
        "morning_thesis": {
            "strategy":    brief.get("strategy_recommendation", "unknown") if brief else "no brief",
            "pcr":         brief.get("oi_analysis", {}).get("pcr", None) if brief else None,
            "max_pain":    brief.get("oi_analysis", {}).get("max_pain", None) if brief else None,
            "range":       brief.get("oi_analysis", {}).get("expected_range", None) if brief else None,
            "regime":      brief.get("regime_signal", None) if brief else None,
        },
        "entry": {
            "strategy":  entry.get("strategy", "unknown") if entry else "no entry",
            "spot":      entry.get("spot_at_entry", 0) if entry else 0,
            "lots":      entry.get("lots", 0) if entry else 0,
            "dry_run":   entry.get("dry_run", True) if entry else True,
        },
        "session_stats": {
            "final_pnl":    stats["final_pnl"],
            "max_pnl":      stats["max_pnl"],
            "min_pnl":      stats["min_pnl"],
            "total_cycles": stats["total_cycles"],
            "action_counts": stats["action_counts"],
        },
        "decisions":          compact_decisions,
        "existing_rules":     existing_rules,
        "output_schema": {
            "session_date":       "string YYYY-MM-DD",
            "final_pnl":          "float",
            "max_intraday_pnl":   "float",
            "min_intraday_pnl":   "float",
            "thesis_accuracy":    "CORRECT|PARTIAL|WRONG",
            "thesis_explanation": "string",
            "entry_quality":      "GOOD|ACCEPTABLE|POOR",
            "entry_explanation":  "string",
            "decision_grades": [
                {
                    "cycle":              "int",
                    "action":             "string",
                    "reasoning_summary":  "string",
                    "grade":              "CORRECT|PREMATURE|LATE|WRONG",
                    "explanation":        "string",
                }
            ],
            "learned_rules": [
                {
                    "rule":       "string — a clear, actionable rule",
                    "condition":  "string — when this rule applies",
                    "action":     "string — what to do",
                    "confidence": "0.0–1.0",
                    "source":     "string — what evidence supports this",
                }
            ],
            "signals_to_boost":  ["list of signal names that worked today"],
            "signals_to_reduce": ["list of signal names that failed today"],
            "tomorrow_context":  "1-paragraph note for tomorrow's agent — what to watch for",
        },
    }

    return json.dumps(prompt)


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------

def call_reflection_agent(prompt: str, client: OpenAI) -> Reflection:
    """Calls gpt-4o and parses the structured reflection JSON."""
    print("  Calling gpt-4o for reflection...", end="", flush=True)
    resp = client.chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {"role": "system",  "content": SYSTEM_PROMPT},
            {"role": "user",    "content": prompt},
        ],
        temperature=0.2,    # low temperature for analytical grading
        max_tokens=2000,
    )
    print(" done")

    raw = resp.choices[0].message.content.strip()
    # Strip markdown code fences if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return Reflection.model_validate(json.loads(raw))


# ---------------------------------------------------------------------------
# Print reflection summary
# ---------------------------------------------------------------------------

def print_reflection(r: Reflection) -> None:
    grade_icons = {"CORRECT": "✓", "PREMATURE": "⚡", "LATE": "⏰", "WRONG": "✗"}

    print(f"\n{'='*58}")
    print(f"  Post-Market Reflection — {r.session_date}")
    print(f"{'='*58}")
    print(f"  Final P&L : ₹{r.final_pnl:,.0f}")
    print(f"  Peak P&L  : ₹{r.max_intraday_pnl:,.0f}  |  Trough: ₹{r.min_intraday_pnl:,.0f}")
    print(f"\n  Thesis    : {r.thesis_accuracy}  — {r.thesis_explanation}")
    print(f"  Entry     : {r.entry_quality}  — {r.entry_explanation}")

    print(f"\n  Decision Grades:")
    for g in r.decision_grades:
        icon = grade_icons.get(g.grade, "?")
        print(f"    Cycle {g.cycle:2d}  {icon} {g.grade:<10}  [{g.action}]  {g.explanation[:70]}")

    print(f"\n  Learned Rules ({len(r.learned_rules)}):")
    for rule in r.learned_rules:
        conf_bar = "█" * int(rule.confidence * 10) + "░" * (10 - int(rule.confidence * 10))
        print(f"    [{conf_bar}] {rule.rule}")
        print(f"               Condition: {rule.condition}")

    if r.signals_to_boost:
        print(f"\n  Boost signals : {', '.join(r.signals_to_boost)}")
    if r.signals_to_reduce:
        print(f"  Reduce signals: {', '.join(r.signals_to_reduce)}")

    print(f"\n  Tomorrow context:")
    for line in r.tomorrow_context.split(". "):
        if line.strip():
            print(f"    {line.strip()}.")
    print(f"{'='*58}")


# ---------------------------------------------------------------------------
# Show memory
# ---------------------------------------------------------------------------

def show_memory() -> None:
    mem = load_memory()
    print(f"\n=== Agent Memory — {mem.get('sessions_analysed', 0)} sessions ===\n")
    rules = mem.get("learned_rules", [])
    if not rules:
        print("  No rules learned yet.")
    else:
        for i, r in enumerate(rules, 1):
            conf_pct = int(r.get("confidence", 0) * 100)
            print(f"  {i:2d}. [{conf_pct:3d}%] {r['rule']}")
            print(f"        When: {r.get('condition','')}")

    print(f"\n  Strategy performance:")
    for strat, perf in mem.get("strategy_performance", {}).items():
        print(
            f"    {strat:<20} {perf.get('sessions',0)} sessions  "
            f"avg ₹{perf.get('avg_pnl',0):,.0f}  "
            f"win {int(perf.get('win_rate',0)*100)}%"
        )

    print(f"\n  Signal accuracy:")
    for sig, acc in mem.get("signal_accuracy", {}).items():
        total = acc.get("total", 0)
        if total:
            pct = int(acc["correct"] / total * 100)
            print(f"    {sig:<30} {pct:3d}%  ({acc['correct']}/{total})")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_post_market(date_str: str, dry_run: bool = False) -> None:

    print(f"\n=== Post-Market Reflection — {date_str} ===")

    # Load data
    brief, entry, decisions = _load_today_data(date_str)

    if not decisions:
        print("  No decision log found — was position_manager.py run today?")
        if not brief and not entry:
            print("  No session data at all. Nothing to reflect on.")
            return

    stats = compute_session_stats(decisions)
    print(
        f"  Loaded: {len(decisions)} decisions  |  "
        f"final P&L ₹{stats['final_pnl']:,.0f}  |  "
        f"actions: {stats['action_counts']}"
    )

    # Load cumulative memory
    memory = load_memory()
    print(f"  Memory: {memory['sessions_analysed']} prior sessions, "
          f"{len(memory.get('learned_rules',[]))} learned rules")

    # Build prompt
    prompt = build_prompt(date_str, brief, entry, decisions, stats, memory)

    if dry_run:
        print("\n[DRY RUN] Prompt (truncated):")
        print(prompt[:800] + "...")
        return

    # Load env + call LLM
    env_path = BASE / ".env"
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip())

    openai_key = os.environ.get("OPENAI_API_KEY", "")
    if not openai_key:
        sys.exit("OPENAI_API_KEY not set in .env")

    client = OpenAI(
        api_key=openai_key,
        timeout=httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=5.0),
    )

    try:
        reflection = call_reflection_agent(prompt, client)
    except Exception as e:
        print(f"  Reflection LLM failed: {e}")
        traceback.print_exc()
        return

    # Print summary
    print_reflection(reflection)

    # Save learning notes
    NOTES_DIR.mkdir(parents=True, exist_ok=True)
    notes_path = NOTES_DIR / f"{date_str}.json"
    notes_path.write_text(json.dumps(reflection.model_dump(), indent=2))
    print(f"\n  Saved learning notes → {notes_path}")

    # Update persistent memory
    strategy = entry.get("strategy", "unknown") if entry else "unknown"
    memory = update_memory(memory, reflection, strategy)
    MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    MEMORY_FILE.write_text(json.dumps(memory, indent=2))
    print(f"  Updated agent memory → {MEMORY_FILE}")
    print(f"  Memory now has {len(memory['learned_rules'])} rules, "
          f"{memory['sessions_analysed']} sessions")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Post-market reflection + agent learning")
    parser.add_argument("--date",        default=datetime.now(IST).strftime("%Y-%m-%d"),
                        help="Date to reflect on (default: today)")
    parser.add_argument("--dry-run",     action="store_true", help="Show prompt, no LLM call")
    parser.add_argument("--show-memory", action="store_true", help="Print accumulated memory")
    args = parser.parse_args()

    if args.show_memory:
        show_memory()
    else:
        run_post_market(date_str=args.date, dry_run=args.dry_run)
