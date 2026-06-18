#!/usr/bin/env python3
"""
test_ta.py — Stage 6 validation script.

Runs TradingAgents on a single NSE stock and prints the structured output.
Takes ~2-3 minutes on first run (LLM calls for all 4 agents + debate).

Usage:
  cd ~/autotrade
  .venv/bin/python agents/test_ta.py
  .venv/bin/python agents/test_ta.py --symbol HDFCBANK.NS
"""
import json
import os
import sys
import argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from ta_config import get_config


def _load_env():
    env_path = Path("/home/freed/autotrade/.env")
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, val = line.partition("=")
                    os.environ.setdefault(key.strip(), val.strip())
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        sys.exit("ERROR: ANTHROPIC_API_KEY not set. Add to ~/autotrade/.env")
    print(f"API key: {key[:8]}...{key[-4:]}")


def test_single(symbol: str, date: str):
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    config = get_config()
    print(f"\nConfig:")
    print(f"  provider : {config.llm_provider}")
    print(f"  deep LLM : {config.deep_think_llm}")
    print(f"  quick LLM: {config.quick_think_llm}")
    print(f"  results  : {config.results_dir}")

    print(f"\nRunning TradingAgents on {symbol} for {date}...")
    print("This takes ~2-3 minutes (4 analyst agents + bull/bear debate + risk judge).\n")

    ta = TradingAgentsGraph(
        config=config,
        selected_analysts=["market", "social", "news", "fundamentals"],
    )

    _state, rec = ta.propagate(symbol, date)

    print("\n" + "=" * 60)
    print("TRADE RECOMMENDATION")
    print("=" * 60)
    print(f"  Signal    : {rec.signal}")
    print(f"  Confidence: {rec.confidence:.2f}")
    print(f"  Size      : {rec.size_fraction:.0%} of capital")
    if rec.entry_reference_price:
        print(f"  Entry ref : {rec.currency} {rec.entry_reference_price:.2f}")
    if rec.target_price:
        print(f"  Target    : {rec.currency} {rec.target_price:.2f}")
    if rec.stop_loss:
        print(f"  Stop loss : {rec.currency} {rec.stop_loss:.2f}")
    if rec.time_horizon_days:
        print(f"  Horizon   : {rec.time_horizon_days} days")
    if rec.warning_message:
        print(f"  WARNING   : {rec.warning_message}")
    print()
    print("RATIONALE:")
    print(rec.rationale)
    print("=" * 60)

    if rec.signal in ("BUY", "SELL", "HOLD") and rec.confidence > 0:
        print("\nSTAGE 6 TEST: PASS — all agents ran, structured recommendation received")
        if rec.warning_message:
            print("  (warning_message is a risk-judge caveat, not an error)")
    else:
        print("\nSTAGE 6 TEST: FAIL — unexpected output")

    return rec


if __name__ == "__main__":
    IST = timezone(timedelta(hours=5, minutes=30))
    today = datetime.now(IST).strftime("%Y-%m-%d")

    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="RELIANCE.NS", help="NSE symbol (default: RELIANCE.NS)")
    parser.add_argument("--date", default=today, help="Trade date YYYY-MM-DD (default: today IST)")
    args = parser.parse_args()

    _load_env()
    test_single(args.symbol, args.date)
