#!/usr/bin/env python3
"""
Write task completion fact to agent_memory table.
Called by claude-dev SOUL.md after each task.

Usage: python3 write_task_memory.py '<task_id>' '<task_title>'
"""
import sys
import subprocess
import os

sys.path.insert(0, '/home/freed/autotrade')
from shared.db import memory_set

task_id = sys.argv[1] if len(sys.argv) > 1 else "unknown"
task_title = sys.argv[2] if len(sys.argv) > 2 else ""

try:
    files = subprocess.check_output(
        ['git', 'diff', '--name-only', 'HEAD~1', 'HEAD'],
        cwd='/home/freed/autotrade',
        stderr=subprocess.DEVNULL
    ).decode().strip().replace('\n', ', ')
except Exception:
    files = ""

value = task_title
if files:
    value += f" | files: {files}"

memory_set(f"task:{task_id}", value, "claude-dev")
print(f"[agent_memory] task:{task_id} = {value}")
