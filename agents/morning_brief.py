#!/usr/bin/env python3
"""
morning_brief.py — 6am IST cron target (Stages 6 + 7).

Runs TradingAgents on equity symbols, then runs the OI Analyst
on BANKNIFTY/NIFTY, and merges both into a single morning_brief.json.

The OI Analyst adds: PCR, max pain, OI walls, expected range,
and a strategy_recommendation that the Entry Executor reads at 9:15am.

Usage:
  python agents/morning_brief.py                        # today, defaults
  python agents/morning_brief.py --date 2026-06-09
  python agents/morning_brief.py --symbols HDFCBANK.NS TCS.NS
  python agents/morning_brief.py --underlying NIFTY     # options on NIFTY
  python agents/morning_brief.py --skip-ta              # OI only (faster test)
"""
import json
import os
import sys
import argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from ta_config import get_config

IST = timezone(timedelta(hours=5, minutes=30))

DEFAULT_SYMBOLS = [
    "RELIANCE.NS",
    "HDFCBANK.NS",
    "INFY.NS",
    "TCS.NS",
    "ICICIBANK.NS",
]
DEFAULT_UNDERLYING = "BANKNIFTY"


MEMORY_FILE = Path("/home/freed/autotrade/data/agent_memory.json")


def _load_env():
    env_path = Path("/home/freed/autotrade/.env")
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, val = line.partition("=")
                    os.environ.setdefault(key.strip(), val.strip())


def _load_prior_learning() -> dict:
    """
    Reads top learned rules from agent_memory.json (written by post_market.py).
    Returns a compact dict injected into the brief so position_manager.py
    and OI analyst can factor in lessons from prior sessions.
    """
    if not MEMORY_FILE.exists():
        return {}
    try:
        mem = json.loads(MEMORY_FILE.read_text())
        top_rules = [
            {"rule": r["rule"], "condition": r["condition"], "confidence": r["confidence"]}
            for r in mem.get("learned_rules", [])[:5]   # top 5 by confidence
        ]
        return {
            "sessions_analysed": mem.get("sessions_analysed", 0),
            "top_rules":         top_rules,
            "strategy_performance": mem.get("strategy_performance", {}),
            "signal_accuracy":   {
                k: v for k, v in mem.get("signal_accuracy", {}).items()
                if v.get("total", 0) >= 3    # only show signals with enough evidence
            },
        }
    except Exception as e:
        print(f"  Warning: could not load agent memory: {e}", flush=True)
        return {}


def _run_ta_symbol(ta, symbol: str, date: str) -> dict:
    print(f"  [TA:{symbol}] running agents...", flush=True)
    try:
        _state, rec = ta.propagate(symbol, date)
        return {
            "signal": rec.signal,
            "confidence": rec.confidence,
            "size_fraction": rec.size_fraction,
            "target_price": rec.target_price,
            "stop_loss": rec.stop_loss,
            "time_horizon_days": rec.time_horizon_days,
            "currency": rec.currency,
            "entry_reference_price": rec.entry_reference_price,
            "rationale": rec.rationale,
            "warning": rec.warning_message,
            "status": "ok",
        }
    except Exception as exc:
        print(f"  [TA:{symbol}] ERROR: {exc}", flush=True)
        return {"status": "error", "error": str(exc)}


def _run_ta_block(symbols: list, date: str) -> dict:
    """Run TradingAgents on equity symbols. Returns per-symbol dict."""
    if not os.environ.get("OPENAI_API_KEY"):
        print("  WARNING: OPENAI_API_KEY not set — skipping TradingAgents", flush=True)
        return {}

    from tradingagents.graph.trading_graph import TradingAgentsGraph
    config = get_config()
    ta = TradingAgentsGraph(
        config=config,
        selected_analysts=["market", "social", "news", "fundamentals"],
    )
    results = {}
    for symbol in symbols:
        results[symbol] = _run_ta_symbol(ta, symbol, date)
    return results


def _summarise_signal(equity_briefs: dict) -> tuple[str, float]:
    """
    Aggregate signal across equity symbols → overall regime signal.
    Returns (dominant_signal, avg_confidence).
    """
    signals = [v.get("signal") for v in equity_briefs.values() if v.get("status") == "ok"]
    confs = [v.get("confidence", 0) for v in equity_briefs.values() if v.get("status") == "ok"]

    if not signals:
        return "HOLD", 0.50

    buy_count = signals.count("BUY")
    sell_count = signals.count("SELL")
    avg_conf = sum(confs) / len(confs) if confs else 0.50

    if buy_count > sell_count and buy_count > len(signals) / 2:
        return "BUY", avg_conf
    if sell_count > buy_count and sell_count > len(signals) / 2:
        return "SELL", avg_conf
    return "HOLD", avg_conf


def run_morning_brief(
    symbols: list = None,
    date: str = None,
    underlying: str = None,
    skip_ta: bool = False,
    expiry_offset: int = 0,
) -> dict:
    _load_env()

    if date is None:
        date = datetime.now(IST).strftime("%Y-%m-%d")
    if symbols is None:
        symbols = DEFAULT_SYMBOLS
    if underlying is None:
        underlying = DEFAULT_UNDERLYING

    print(f"\n=== Morning Brief: {date} | underlying={underlying} ===", flush=True)

    # Load accumulated agent learning from prior sessions
    prior_learning = _load_prior_learning()
    if prior_learning.get("sessions_analysed"):
        print(
            f"  Prior learning: {prior_learning['sessions_analysed']} sessions · "
            f"{len(prior_learning.get('top_rules', []))} rules loaded",
            flush=True,
        )

    # --- Block 1: TradingAgents equity analysis ---
    equity_briefs = {}
    if not skip_ta:
        print("\n[1/2] Running TradingAgents on equity symbols...", flush=True)
        equity_briefs = _run_ta_block(symbols, date)

        print("\n--- Equity Summary ---")
        for sym, r in equity_briefs.items():
            if r.get("status") == "ok":
                print(f"  {sym:20s}  {r['signal']:4s}  conf={r['confidence']:.2f}", flush=True)
            else:
                print(f"  {sym:20s}  ERROR", flush=True)
    else:
        print("[1/2] TradingAgents skipped (--skip-ta)", flush=True)

    # Derive overall regime signal from equity briefs
    regime_signal, regime_confidence = _summarise_signal(equity_briefs)
    print(f"\nOverall regime: {regime_signal} (confidence {regime_confidence:.2f})", flush=True)

    # --- Block 2: OI Analyst ---
    oi_data = None
    api_key = os.environ.get("OPENALGO_API_KEY", "")

    if api_key:
        print(f"\n[2/2] Running OI Analyst on {underlying}...", flush=True)
        try:
            from oi_analyst import run_oi_analysis
            oi_result = run_oi_analysis(
                underlying=underlying,
                api_key=api_key,
                expiry_offset=expiry_offset,
                ta_signal=regime_signal,
                ta_confidence=regime_confidence,
            )
            oi_data = oi_result.model_dump()
            print(f"  OI: PCR={oi_data['pcr']} | max_pain={oi_data['max_pain']} "
                  f"| range={oi_data['expected_range_str']} "
                  f"| strategy={oi_data['strategy_recommendation']}", flush=True)
        except Exception as e:
            print(f"  OI Analyst ERROR: {e}", flush=True)
    else:
        print("[2/2] OI Analyst skipped (OPENALGO_API_KEY not set)", flush=True)

    # --- Assemble final brief ---
    strategy_recommendation = (
        oi_data["strategy_recommendation"] if oi_data else "iron_condor"
    )
    strategy_reason = (
        oi_data["strategy_reason"] if oi_data else "OI data unavailable — default to iron_condor"
    )

    brief = {
        "date": date,
        "generated_at": datetime.now(IST).isoformat(),
        "underlying": underlying,

        # Top-level session fields (Entry Executor reads these)
        "strategy_recommendation": strategy_recommendation,
        "strategy_reason": strategy_reason,
        "regime_signal": regime_signal,
        "regime_confidence": round(regime_confidence, 3),

        # Detailed blocks
        "oi_analysis":    oi_data,
        "equity_briefs":  equity_briefs,

        # Lessons from prior sessions (written by post_market.py, fed back here)
        # position_manager.py can inject these into its system prompt context
        "prior_learning": prior_learning,
    }

    # Save
    config = get_config()
    output_dir = config.results_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / f"{date}.json"
    with open(output_file, "w") as f:
        json.dump(brief, f, indent=2, default=str)

    print(f"\nBrief saved → {output_file}")
    print(f"Strategy recommendation: {strategy_recommendation}")
    return brief


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="Trade date YYYY-MM-DD (default: today IST)")
    parser.add_argument("--symbols", nargs="+", help="NSE equity symbols")
    parser.add_argument("--underlying", default=DEFAULT_UNDERLYING,
                        choices=["BANKNIFTY", "NIFTY"], help="Index for options OI")
    parser.add_argument("--skip-ta", action="store_true",
                        help="Skip TradingAgents, run OI only (fast test)")
    parser.add_argument("--expiry-offset", type=int, default=0,
                        help="0=nearest upcoming expiry, 1=upcoming+1 (default: 0)")
    args = parser.parse_args()
    run_morning_brief(
        symbols=args.symbols,
        date=args.date,
        underlying=args.underlying,
        skip_ta=args.skip_ta,
        expiry_offset=args.expiry_offset,
    )
