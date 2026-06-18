"""
test_pm_api.py — Debug test: verify OpenAI API responds to position manager prompt.
Run: cd ~/autotrade && .venv/bin/python agents/test_pm_api.py
"""
import os, json
from pathlib import Path
from openai import OpenAI
import httpx
import time

# Load .env
with open(Path('/home/freed/autotrade/.env')) as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, _, v = line.partition('=')
            os.environ.setdefault(k.strip(), v.strip())

client = OpenAI(
    api_key=os.environ['OPENAI_API_KEY'],
    timeout=httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=5.0)
)

SYSTEM = (
    "You are an expert Indian options position manager. "
    "Respond with ONLY valid JSON, no markdown. "
    'Schema: {"action":"HOLD or HEDGE_DELTA","instrument":null,'
    '"quantity":null,"direction":null,"price_type":"MARKET","price":null,'
    '"reasoning":"one sentence","urgency":"low or medium or high",'
    '"next_review":"5min or 15min or 30min"}'
)

USER = json.dumps({
    "goal": {
        "strategy": "iron_condor",
        "underlying": "BANKNIFTY",
        "target_profit": 8000,
        "max_loss": -6000,
        "expiry": "2025-06-26",
        "style": "conservative",
    },
    "context": {
        "pnl_inr": -800,
        "net_delta": 0.22,
        "net_theta_per_day": 16.5,
        "net_vega": -53.0,
        "underlying_price": 55150,
        "underlying_move_pts": 150,
        "vix": 16.2,
        "pcr": 0.82,
        "pcr_trend": "falling",
        "time_to_expiry_hours": 4.5,
        "oi_shift": "CE wall at +500: OI buildup +12%",
        "morning_signal": "HOLD",
        "open_legs": [
            {"symbol": "BANKNIFTY25JUN55500CE", "qty": -1, "pnl": -15},
            {"symbol": "BANKNIFTY25JUN54500PE", "qty": -1, "pnl": 12},
            {"symbol": "BANKNIFTY25JUN55800CE", "qty": 1, "pnl": -5},
            {"symbol": "BANKNIFTY25JUN54200PE", "qty": 1, "pnl": -5},
        ],
    }
})

print("Sending request to gpt-4o...")
t0 = time.time()
try:
    resp = client.chat.completions.create(
        model='gpt-4o',
        max_tokens=256,
        messages=[
            {'role': 'system', 'content': SYSTEM},
            {'role': 'user', 'content': USER},
        ]
    )
    elapsed = time.time() - t0
    print(f"Response received in {elapsed:.1f}s")
    print(resp.choices[0].message.content)
except Exception as e:
    elapsed = time.time() - t0
    print(f"FAILED after {elapsed:.1f}s: {e}")
