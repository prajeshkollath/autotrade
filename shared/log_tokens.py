#!/usr/bin/env python3
"""
Print token usage summary for a completed task.
Reads Claude Code JSONL (most recent project log) and prints totals.
Called by claude-dev SOUL.md after each task completes.

Usage: python3 log_tokens.py '<task_id>' '<task_title>'
"""

import sys
import os
import json
import glob

def find_latest_claude_jsonl():
    """Find the most recently modified JSONL in ~/.claude/projects/"""
    pattern = os.path.expanduser("~/.claude/projects/**/*.jsonl")
    files = glob.glob(pattern, recursive=True)
    if not files:
        return None
    return max(files, key=os.path.getmtime)

def sum_claude_tokens(jsonl_path):
    """Sum input/output/cache tokens from a Claude Code JSONL session file."""
    totals = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    try:
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                usage = obj.get("usage") or obj.get("message", {}).get("usage", {})
                if not usage:
                    continue
                totals["input"] += usage.get("input_tokens", 0)
                totals["output"] += usage.get("output_tokens", 0)
                totals["cache_read"] += usage.get("cache_read_input_tokens", 0)
                totals["cache_write"] += usage.get("cache_creation_input_tokens", 0)
    except Exception:
        pass
    return totals

def main():
    task_id = sys.argv[1] if len(sys.argv) > 1 else "unknown"
    task_title = sys.argv[2] if len(sys.argv) > 2 else ""

    jsonl = find_latest_claude_jsonl()
    tokens = sum_claude_tokens(jsonl) if jsonl else {}

    inp = tokens.get("input", 0)
    out = tokens.get("output", 0)
    cr = tokens.get("cache_read", 0)
    cw = tokens.get("cache_write", 0)
    total = inp + out + cr + cw

    print(f"\n=== TOKEN USAGE [{task_id}] {task_title} ===")
    print(f"  Claude input:       {inp:>8,}")
    print(f"  Claude output:      {out:>8,}")
    print(f"  Cache read:         {cr:>8,}")
    print(f"  Cache write:        {cw:>8,}")
    print(f"  Total (all):        {total:>8,}")
    if jsonl:
        print(f"  Source: {os.path.basename(jsonl)}")
    print("=" * 45)

if __name__ == "__main__":
    main()
