# Autotrade — System Architecture

> Last updated: 2026-06-18

---

## What This System Does

An autonomous AI trading system for NSE/BSE equities and options (F&O).
- **Morning brief at 6am IST** — AI analysts research market conditions, OI, VIX, and generate a structured recommendation
- **Strategy entry at 9:15am IST** — agent enters positions via broker API based on morning brief recommendation
- **Position management loop (every minute, 9:15–3:30pm IST)** — GPT-4o reads live market context, decides HOLD / ADJUST / EXIT, executes via broker
- **Post-market (3:30pm IST)** — EOD summary, P&L, decision log review
- **Backtesting** — replay any historical day through the same live agent pipeline

All in **sandbox/paper mode** now. Switch to live with `EXECUTION_MODE=live` in `.env`.

---

## Infrastructure (GCP VM — us-central1-a)

```
instance-20260525-143559   e2-medium   4GB RAM   50GB SSD   IP: 34.45.46.60
```

### Processes Running on VM

| Process | Port | How It Starts |
|---------|------|--------------|
| OpenAlgo (broker bridge) | 5000 | `~/openalgo/start.sh` |
| Autotrade web dashboard | 8080 | `./start_dashboard.sh` (screen session) |
| PostgreSQL (Docker) | 5432 | `docker compose up -d` |
| ExpiryFlow backend | varies | `~/ExpiryFlow/backend/` |

---

## External Platforms

### OpenAlgo (`~/openalgo/`)
Flask web app running at `http://localhost:5000`. Acts as the broker bridge layer.

- Connects to **Zerodha Kite Connect** (live broker API + WebSocket market data)
- Exposes a clean REST API consumed by all trading agents:
  - `GET /api/v1/positions` — open positions
  - `GET /api/v1/tradebook` — filled trades today
  - `POST /api/v1/placeorder` — place market/limit order
  - `GET /api/v1/optionchain` — full option chain with OI
  - `GET /api/v1/quotes` — live LTP for any symbol
- **Analyze Mode** (sandbox): routes orders to SQLite sandbox instead of live broker
- Own SQLite DB at `~/openalgo/db/openalgo.db` (instrument master, token map)
- Web UI at `http://34.45.46.60:5000`

### ExpiryFlow (`~/ExpiryFlow/`)
Options data and analytics platform.

- Options chain, greeks (delta/theta/vega/gamma), expiry calendar
- The `adapters/expiryflow_bridge/` in autotrade wraps its API:
  - `bars.py` — historical bars for options
  - `greeks.py` — Black-Scholes greeks per leg
  - `instruments.py` — symbol/token lookup
  - `expiry_calendar.py` — next/current expiry dates
  - `convert.py` — symbol format conversion (OpenAlgo ↔ NSE)

---

## Python Stack (autotrade `.venv`)

| Package | Version | Role |
|---------|---------|------|
| **nautilus_trader** | 1.227.0 | Trading engine framework — data catalog, backtesting, adapter base classes |
| **vectorbt** | 0.28.4 | Strategy backtesting (signal-based, vectorized) |
| **backtrader** | 1.9.78.123 | Alternative backtesting framework |
| **duckdb** | 1.5.3 | Analytics queries on Parquet data |
| **TA-Lib** | 0.6.8 | Technical indicators (RSI, EMA, MACD, Bollinger etc) |
| **openalgo** | 1.0.45 | OpenAlgo Python client |
| **Flask** | 3.1.3 | Web dashboard |
| **FastAPI** | 0.136.3 | API layer (available, not yet wired) |
| **openai** | 2.41.0 | GPT-4o for position management decisions |
| **anthropic** | 0.107.1 | Claude — available, not yet wired into agents |
| **langchain-anthropic** | 1.4.4 | LangChain + Claude |
| **langchain-openai** | 1.2.2 | LangChain + OpenAI |
| **chainlit** | 2.11.1 | Chat UI (available for future agent interface) |
| **pandas / numpy / scipy** | latest | Data manipulation |
| **pyarrow** | 24.0.0 | Parquet read/write (Nautilus data catalog) |
| **rich** | 15.0.0 | Terminal dashboard (paper_dashboard.py) |

---

## Repository Structure

```
autotrade/
│
├── agents/                         ← All trading agents
│   ├── morning_brief.py            # 6am: TradingAgents + OI Analyst → morning_brief.json
│   ├── oi_analyst.py               # PCR, max pain, OI walls, expected range, strategy rec
│   ├── start_strategy.py           # Entry: reads strategies.json → places CE+PE sells
│   ├── position_manager.py         # Loop (every min): GPT-4o context → decision → execute
│   ├── context_builder.py          # Builds ContextSnapshot (positions, VIX, OI, VWAP)
│   ├── decision_executor.py        # Maps Decision JSON → OpenAlgo REST orders
│   ├── decision_logger.py          # Writes every decision to data/decision_logs/ JSONL
│   ├── entry_executor.py           # Entry logic with risk checks
│   ├── session_memory.py           # In-session state across agents (module-level)
│   ├── goal_schema.py              # Pydantic schemas: Decision, Goal, ContextSnapshot
│   ├── ta_config.py                # TradingAgents config (GPT-4o + GPT-4o-mini)
│   ├── web_dashboard.py            # Flask dashboard at :8080 — Trading/Screener/Strategies
│   ├── paper_dashboard.py          # Rich terminal dashboard for live paper session
│   ├── mcx_strangle.py             # MCX options strangle (Gold, Silver, Crude)
│   ├── rs_screener.py              # Relative strength screener vs Nifty 50
│   ├── screener_generator.py       # Intraday momentum screener
│   ├── backtest_replay.py          # Replay any historical day through live agent pipeline
│   ├── post_market.py              # EOD P&L summary and decision log review
│   └── web_dashboard.py            # Flask: /trading /screener /strategies views
│
├── adapters/                       ← External API bridges
│   ├── openalgo/                   # Nautilus Trader adapter for OpenAlgo
│   │   ├── data_client.py          # NT LiveDataClient — quotes, bars, option chain
│   │   ├── execution_client.py     # NT LiveExecutionClient — place/cancel/modify orders
│   │   ├── instrument_provider.py  # NT InstrumentProvider — symbol/token resolution
│   │   ├── factory.py              # NT LiveDataClientFactory + LiveExecClientFactory
│   │   └── config.py               # NT LiveDataClientConfig + LiveExecClientConfig
│   └── expiryflow_bridge/          # ExpiryFlow options data bridge
│       ├── bars.py
│       ├── greeks.py
│       ├── instruments.py
│       ├── expiry_calendar.py
│       └── convert.py
│
├── strategies/                     ← Strategy implementations
│   ├── equity/ema_crossover.py
│   └── options/iron_condor.py
│
├── shared/                         ← Shared across all agents
│   ├── db.py                       # PostgreSQL helpers (agent_memory read/write)
│   ├── log_tokens.py               # Print Claude Code token usage per task
│   └── write_task_memory.py        # Write task facts to agent_memory table
│
├── migrations/init.sql             ← DB schema (agent_memory, task_tokens)
├── docker-compose.yml              ← PostgreSQL container
├── start_dashboard.sh              ← Launch web dashboard in screen session
└── data/                           ← Runtime data (NOT in git)
    ├── catalog/                    # Nautilus Trader Parquet data catalog (297MB)
    ├── decision_logs/              # Per-session decision JSONL (2.4MB)
    ├── morning_briefs/             # Daily morning_brief.json files
    ├── session_memory/             # In-session state snapshots
    ├── screener/                   # Screener output
    └── strategies.json             # Active strategy configs
```

---

## Active Strategies (`data/strategies.json`)

| ID | Name | Type | Underlying | Mode |
|----|------|------|-----------|------|
| `nifty_short_strangle` | NIFTY Short Strangle | Options (NFO) | NIFTY | sandbox |
| `goldm_short_strangle` | GOLDM Short Strangle | Options (MCX) | GOLDM | sandbox |
| `adanient_equity_long` | ADANIENT Equity Long | Equity | ADANIENT | sandbox |
| `hindalco_equity_long` | HINDALCO Equity Long | Equity | HINDALCO | sandbox |
| `adaniports_equity_long` | ADANIPORTS Equity Long | Equity | ADANIPORTS | sandbox |

---

## Full Daily Flow

```
6:00 AM IST
  └─ morning_brief.py
       ├─ TradingAgents (GPT-4o + GPT-4o-mini)
       │    ├─ Bull/Bear analysts debate market
       │    ├─ Portfolio Manager synthesises
       │    └─ Writes equity signal JSON
       └─ oi_analyst.py (NIFTY / BANKNIFTY)
            ├─ Reads option chain via OpenAlgo → Zerodha
            ├─ Computes PCR, max pain, OI walls, expected range
            └─ Recommends: iron_condor / short_straddle / hold
       → output: data/morning_briefs/YYYY-MM-DD.json

9:15 AM IST
  └─ start_strategy.py --id nifty_short_strangle
       ├─ Reads data/strategies.json for config
       ├─ Checks OpenAlgo positionbook (skip if already in position)
       ├─ Scans option chain → selects OTM CE + PE strikes
       ├─ Places SELL orders via OpenAlgo → Zerodha (or sandbox)
       └─ Hands off to position_manager via os.execv

9:15 AM – 3:30 PM IST  (every 60 seconds)
  └─ position_manager.py
       ├─ HARD STOP check (instant, no LLM — if max_loss breached → exit all)
       ├─ BEHAVIORAL RULES (pre-LLM — delta spike, OTM breach, time decay)
       ├─ context_builder.py
       │    ├─ OpenAlgo: live positions + LTP + greeks
       │    ├─ yfinance: VIX (^INDIAVIX)
       │    └─ OpenAlgo: PCR + OI walls snapshot
       ├─ GPT-4o prompt: goal + rules + ContextSnapshot → Decision JSON
       │    Decision types: HOLD / SHIFT_STRIKE / PARTIAL_EXIT /
       │                    FULL_EXIT / ADD_POSITION / HEDGE_DELTA
       ├─ decision_executor.py → OpenAlgo REST → Zerodha
       └─ decision_logger.py → data/decision_logs/YYYY-MM-DD_<id>.jsonl

3:30 PM IST
  └─ post_market.py
       ├─ Query OpenAlgo tradebook for all fills
       ├─ Compute P&L, win/loss, avg holding time
       └─ Write EOD summary

Anytime — Agent replay (backtest_replay.py)
  └─ backtest_replay.py --date YYYY-MM-DD
       ├─ Loads NT Parquet catalog (data/catalog/) for historical option bars
       ├─ Feeds bars into the live agent pipeline (position_manager, decision_executor)
       ├─ No real orders placed — simulates what the agent would have done
       └─ Produces decision log for review at data/decision_logs/

Anytime — Strategy backtest (iron_condor.py)
  └─ strategies/options/iron_condor.py --from 2025-09-01 --to 2026-06-03
       ├─ Queries ExpiryFlow DuckDB (options_data.duckdb) for strikes + greeks
       ├─ Loads NT Parquet catalog for OHLCV bars
       ├─ Runs NT BacktestEngine → Strategy class handles on_bar() events
       └─ Produces full P&L, trade history, metrics
```

---

## LLM Usage

| Agent | Model | Purpose |
|-------|-------|---------|
| `position_manager.py` | GPT-4o | Every-minute trade decisions |
| `morning_brief.py` (via TradingAgents) | GPT-4o | Portfolio manager, risk analyst |
| `morning_brief.py` (via TradingAgents) | GPT-4o-mini | Bull/Bear/Fundamentals analysts |
| Claude Code (Claude Sonnet) | claude-sonnet-4-6 | All development tasks |

**Note**: `anthropic` SDK is installed. Plan is to migrate position management to Claude once tested.

---

## Two Separate Execution Paths

The system has two distinct paths that do **not** share code today:

### Path 1 — Live / Sandbox (custom agents)

```
agents/position_manager.py  (GPT-4o decides every 60s)
    └── agents/decision_executor.py
            └── requests.post("http://localhost:5000/api/v1/placeorder")
                    └── OpenAlgo (localhost:5000)
                            ├── Analyze Mode ON  → SQLite sandbox (no real fills)
                            └── Analyze Mode OFF → Zerodha Kite Connect (live)
```

No Nautilus Trader in this path. Pure Python + OpenAlgo REST.

### Path 2 — Strategy Backtesting (Nautilus Trader + DuckDB)

Used by `strategies/options/iron_condor.py` (and future strategies):

```
ExpiryFlow/backend/options_data.duckdb  (240MB)
    └── Queried via DuckDB to get:
         - Monday spot prices → which strikes to trade each week
         - Greeks at entry/exit (delta/theta/vega from Black-Scholes)
         - Instrument definitions for the 4 legs

data/catalog/  (297MB — Nautilus Trader Parquet)
    └── Loaded via NT ParquetDataCatalog to get:
         - Historical OHLCV bars for each option contract

NT BacktestEngine
    └── Runs the Strategy class bar-by-bar
         - on_bar() → entry/exit logic
         - Places virtual orders → tracks fills, P&L
         - Produces full trade history + metrics
```

The iron condor backtest (`strategies/options/iron_condor.py`) is the only strategy
using this path. Run with:
```bash
cd ~/autotrade
.venv/bin/python strategies/options/iron_condor.py --from 2025-09-01 --to 2026-06-03
```

### The Gap

The same strategy logic can't run in both paths without a rewrite.
`position_manager.py` is built for live trading (loop + LLM + OpenAlgo REST).
`iron_condor.py` is built for backtesting (NT Strategy class + DuckDB + Parquet).

**Future option**: migrate `position_manager.py` to a proper NT `Strategy` class,
then plug in `adapters/openalgo/` as the live venue. Same code would then run
in both `BacktestEngine` and `TradingNode`. The adapter is already built for this.

### Nautilus Trader Components — What's Used vs Built

| Component | Status | Used By |
|-----------|--------|---------|
| `BacktestEngine` | **Used** | `strategies/options/iron_condor.py` |
| `ParquetDataCatalog` | **Used** | `iron_condor.py`, `backtest_replay.py` |
| `adapters/openalgo/` (LiveDataClient + LiveExecClient) | **Built, not wired** | Nothing yet — ready for NT live trading |
| `TradingNode` | **Not used** | Future — would replace the manual loop |

### VectorBT (equity backtesting)

Separate from NT. Used for equity strategy backtesting via `skills/vectorbt/`:
- Reads data from yfinance / CSV
- Signal-based vectorized backtests (EMA crossover, RSI, dual momentum, etc.)
- Does not use DuckDB or NT catalog — independent stack

---

## Data Storage

| Data | Location | Size | In Git |
|------|----------|------|--------|
| Source code, strategies | GitHub repo | ~2MB | Yes |
| Historical options data (DuckDB) | `~/ExpiryFlow/backend/options_data.duckdb` | 240MB | No — ExpiryFlow |
| Historical OHLCV bars (NT Parquet) | `data/catalog/` | 297MB | No |
| Daily decision logs | `data/decision_logs/` JSONL | 2.4MB | No — runtime |
| Morning briefs | `data/morning_briefs/` JSON | 536KB | No — runtime |
| Active strategy configs | `data/strategies.json` | <1KB | No — runtime state |
| Cross-agent memory | PostgreSQL `agent_memory` | — | No — DB |
| Task token logs | PostgreSQL `task_tokens` | — | No — DB |
| OpenAlgo instrument master | `~/openalgo/db/openalgo.db` | — | No — external |

---

## Execution Mode

Controlled by `EXECUTION_MODE` in `.env`:

| Mode | What Happens |
|------|-------------|
| `paper` / `sandbox` | Orders sent to OpenAlgo Analyze Mode — recorded in SQLite, never reach broker |
| `live` | Real orders sent to Zerodha via OpenAlgo |

---

## Key Environment Variables (`.env`)

```bash
OPENALGO_API_KEY=<key>          # OpenAlgo auth
OPENAI_API_KEY=<key>            # GPT-4o for position manager + morning brief
ANTHROPIC_API_KEY=<key>         # Claude — installed, not yet wired to agents
POSTGRES_PASSWORD=<pass>
DATABASE_URL=postgresql://autotrade:<pass>@localhost:5432/autotrade
EXECUTION_MODE=paper
DASHBOARD_PASSWORD=<pass>       # Web dashboard login
```
