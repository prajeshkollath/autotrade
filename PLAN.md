# Autotrade — Architecture & Build Plan

> Living document. Last updated: 2026-06-18

---

## What We Are Building

An autonomous trading system for NSE/BSE equities and options.
- **Data source**: OpenAlgo (broker bridge) + ExpiryFlow (options data)
- **Execution**: Paper trading now, live trading via OpenAlgo → Zerodha when ready
- **AI layer**: Claude Code writes and runs all trading logic
- **Interface**: Web dashboard (Flask, port 8080) + Telegram notifications

---

## Current Stack (on GCP VM — us-central1-a)

| Component | Location | Status |
|-----------|----------|--------|
| OpenAlgo | `~/openalgo/` | Running — broker API bridge |
| ExpiryFlow | `~/ExpiryFlow/` | Running — options chain data |
| PostgreSQL 16 | Docker (`autotrade-postgres`) | Running — shared DB |
| Web dashboard | `agents/web_dashboard.py` | Built — Flask, port 8080 |
| Paper dashboard | `agents/paper_dashboard.py` | Built — terminal UI |
| Claude Code CLI | `/usr/bin/claude` | Running — executes all AI tasks |

---

## Repository Structure

```
autotrade/
├── agents/                     ← Trading agents
│   ├── web_dashboard.py        # Flask dashboard — Trading / Screener / Strategies
│   ├── paper_dashboard.py      # Terminal dashboard for paper session
│   ├── decision_executor.py    # Reads signals → places orders via OpenAlgo
│   ├── position_manager.py     # Tracks open positions, trailing stops, exits
│   ├── context_builder.py      # Builds market context (VIX, breadth, VWAP)
│   ├── entry_executor.py       # Entry logic with risk checks
│   ├── morning_brief.py        # Pre-market summary
│   ├── oi_analyst.py           # Open interest analysis
│   ├── mcx_strangle.py         # MCX options strangle strategy
│   ├── screener_generator.py   # Intraday screener
│   ├── rs_screener.py          # Relative strength screener
│   ├── post_market.py          # EOD summary and P&L
│   ├── backtest_replay.py      # Replay decisions on historical data
│   ├── session_memory.py       # In-session state across agents
│   ├── start_strategy.py       # Strategy launcher
│   ├── ta_config.py            # TA indicator config
│   ├── decision_logger.py      # Logs every decision to data/decision_logs/
│   └── goal_schema.py          # Risk/goal config schema
│
├── adapters/                   ← External API bridges
│   ├── openalgo/               # OpenAlgo REST API client
│   │   ├── data_client.py      # OHLCV, quotes, option chain
│   │   ├── execution_client.py # Place/modify/cancel orders
│   │   ├── instrument_provider.py
│   │   ├── factory.py
│   │   └── config.py
│   └── expiryflow_bridge/      # ExpiryFlow options data
│       ├── bars.py
│       ├── greeks.py
│       ├── instruments.py
│       ├── expiry_calendar.py
│       └── convert.py
│
├── strategies/                 ← Strategy implementations
│   ├── equity/
│   │   └── ema_crossover.py
│   └── options/
│       └── iron_condor.py
│
├── shared/                     ← Shared utilities
│   ├── db.py                   # PostgreSQL helpers (agent_memory read/write)
│   ├── log_tokens.py           # Print Claude Code token usage after each task
│   └── write_task_memory.py    # Write task completion to agent_memory table
│
├── migrations/
│   └── init.sql                # agent_memory + task_tokens tables
│
├── docs/
│   ├── memory-layers.md
│   └── agent_decision_rules.md
│
├── gcp/
│   └── vm-details.md
│
├── docker-compose.yml          ← PostgreSQL container
└── .env.example
```

---

## Database (PostgreSQL — Docker)

```
host: localhost:5432
db:   autotrade
user: autotrade
pass: (in .env)
```

### Live tables

| Table | Purpose |
|-------|---------|
| `agent_memory` | Cross-session key-value store — all agents read/write |
| `task_tokens` | Per-task Claude Code token usage log |

### Planned tables (next)

| Table | Purpose |
|-------|---------|
| `trades` | Every decision with full reasoning trace |
| `positions` | Open positions state |
| `ohlcv` | Historical market data (TimescaleDB hypertable) |
| `strategy_performance` | Aggregated win rate, Sharpe, drawdown per strategy |
| `backtest_results` | Backtest run history |

---

## Data Flow

```
OpenAlgo (broker bridge)
    └── adapters/openalgo/  ←→  agents/decision_executor.py
                                agents/entry_executor.py
                                agents/position_manager.py

ExpiryFlow (options data)
    └── adapters/expiryflow_bridge/  ←→  agents/oi_analyst.py
                                         agents/mcx_strangle.py

agents/web_dashboard.py  →  http://<VM>:8080  (Trading / Screener / Strategies)
agents/paper_dashboard.py  →  terminal UI during paper session

All decisions  →  data/decision_logs/  (JSONL, not in git)
Agent memory  →  PostgreSQL agent_memory table
```

---

## Execution Mode

Controlled by `EXECUTION_MODE` in `.env`:
- `paper` — orders routed to OpenAlgo Analyze Mode (sandbox, no real fills)
- `live` — real orders sent to broker

---

## What's Working Now

- [x] Web dashboard running — positions, screener, strategies views
- [x] Paper dashboard (terminal) for live session monitoring
- [x] OpenAlgo adapter — quotes, OHLCV, order placement
- [x] ExpiryFlow bridge — options chain, greeks, expiry calendar
- [x] Decision executor + position manager + entry executor
- [x] Morning brief, OI analyst, screener, post-market agents
- [x] MCX strangle strategy
- [x] EMA crossover (equity), Iron condor (options) strategies
- [x] agent_memory table — Claude Code writes task facts after each run
- [x] Claude Code credential auto-refresh (daily cron + Secret Manager on restart)

---

## What's Next

- [ ] `trades` table — log every decision with full reasoning to DB
- [ ] EOD P&L report — query DB → markdown → commit → Telegram
- [ ] TimescaleDB upgrade for OHLCV hypertable
- [ ] Zerodha Kite Connect wired to OpenAlgo for live execution
- [ ] Scheduled intraday runs (Claude Code triggered on market schedule)
- [ ] Backtest framework against historical OHLCV
