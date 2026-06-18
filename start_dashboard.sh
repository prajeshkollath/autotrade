#!/bin/bash
# Start/restart the trading dashboard
cd /home/freed/autotrade
screen -S webdash -X quit 2>/dev/null || true
sleep 1
screen -dmS webdash bash -c 'cd /home/freed/autotrade && set -a && source .env && set +a && .venv/bin/python3.12 agents/web_dashboard.py 2>&1 | tee /tmp/webdash.log'
sleep 2
echo 'Dashboard started. Check: curl http://localhost:8080/api/data'
screen -ls
