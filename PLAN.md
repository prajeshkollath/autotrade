# Autotrade — Architecture & Build Plan

> Living document. Updated as the project evolves.
> Last updated: 2026-05-26

---

## Vision

An autonomous trading system operated by three AI teams — a **Dev Team**, a **Trading Desk**, and a **Backtesting Team** — all orchestrated by Hermes and powered by Claude Code as the primary LLM engine.

---

## Guiding Principles

- **Hermes (Qwen) = thin router only** — receives instructions, delegates, reports back. No heavy reasoning.
- **Claude Code = the brain** — all development, all trading decisions, all backtesting. Maximises the Claude Pro subscription.
- **The repo is for code only** — source code, strategy implementations, config, and thin daily summaries. No bulk data.
- **PostgreSQL + TimescaleDB = shared memory** — all trade data, agent memory, market data, and performance metrics live in the DB. All agents read and write from it.
- **Paper → Real with a flag** — every execution path built for real trading from day 1, gated by `EXECUTION_MODE=paper|live`.

---

## System Architecture

```
                        YOU
                         │ Telegram / Web Chat
                         ▼
                  HERMES / QWEN3-235B
             ┌──────────┼────────────┐
             │          │            │
          [dev]     [trade]     [backtest]
             │          │            │
        Claude      Claude       Claude
        Code        Code         Code
        agents      agents       agents
             │          │            │
             └──────────┴────────────┘
                    │           │
              GIT REPO     PostgreSQL + TimescaleDB
           (code only)     (all data — shared memory)
```

---

## Storage Architecture

### What Goes Where

| Data | Storage | Why |
|------|---------|-----|
| Source code, strategies, config | **Git repo** | Version controlled, reviewable, diffable |
| Trade decision logs + reasoning | **PostgreSQL** | High frequency writes, queryable, never bloats repo |
| Open positions, portfolio state | **PostgreSQL** | Real-time mutable state |
| OHLCV market data | **TimescaleDB** | Time-series optimised, auto-partitioned by date |
| Strategy performance metrics | **PostgreSQL** | Aggregatable — win rate, Sharpe, drawdown |
| Backtest results | **PostgreSQL** | Queryable across strategies and time ranges |
| Cross-agent shared memory | **PostgreSQL** | All agents can read/write — replaces Hermes file memory |
| Daily P&L summary | **Git** (one markdown per day) | Human-readable, reviewable, tiny |
| Backtest summary | **Git** (one markdown per run) | For review and strategy decisions |

### Memory Layers

| Layer | What it stores | Who writes | Who reads |
|-------|----------------|------------|-----------|
| **PostgreSQL: `agent_memory`** | Cross-session insights, strategy learnings, agent observations | All Claude Code agents, Hermes | All agents on next run |
| **PostgreSQL: `trades`** | Every decision + full reasoning trace | Execution Agent | All agents, Hermes |
| **TimescaleDB: `ohlcv`** | Historical + live market data | Data Agent | Strategy Agent, Backtest Agent |
| **PostgreSQL: `strategy_performance`** | Aggregated stats per strategy | Updated after each trade/backtest | Risk Agent, Strategy Agent |
| **Git repo** | Code, validated strategies, summaries | Dev agents | All agents |
| **Claude Code run context** | In-run working memory | Claude Code itself | Within single run only |

---

## Database Schema (draft)

```sql
-- Every trade decision with full reasoning trace
CREATE TABLE trades (
    id              SERIAL PRIMARY KEY,
    timestamp       TIMESTAMPTZ NOT NULL,
    symbol          TEXT NOT NULL,
    strategy        TEXT NOT NULL,
    market_context  JSONB,          -- nifty level, VIX, breadth
    signals         JSONB,          -- rsi, vwap_dev, volume_ratio etc
    reasoning       TEXT,           -- full agent reasoning text
    decision        TEXT,           -- BUY / SELL / HOLD
    confidence      NUMERIC(4,2),
    entry_price     NUMERIC(12,2),
    stop_loss       NUMERIC(12,2),
    target          NUMERIC(12,2),
    risk_reward     NUMERIC(6,2),
    position_size   INTEGER,
    risk_amount_inr NUMERIC(12,2),
    execution_mode  TEXT DEFAULT 'paper',   -- paper | live
    status          TEXT DEFAULT 'open',    -- open | closed | cancelled
    exit_price      NUMERIC(12,2),
    pnl_inr        NUMERIC(12,2),
    duration_mins   INTEGER,
    closed_at       TIMESTAMPTZ
);

-- Current open positions
CREATE TABLE positions (
    id           SERIAL PRIMARY KEY,
    trade_id     INTEGER REFERENCES trades(id),
    symbol       TEXT NOT NULL,
    strategy     TEXT NOT NULL,
    entry_time   TIMESTAMPTZ,
    entry_price  NUMERIC(12,2),
    quantity     INTEGER,
    stop_loss    NUMERIC(12,2),
    target       NUMERIC(12,2),
    mode         TEXT DEFAULT 'paper',
    status       TEXT DEFAULT 'open'
);

-- OHLCV market data (TimescaleDB hypertable)
CREATE TABLE ohlcv (
    timestamp    TIMESTAMPTZ NOT NULL,
    symbol       TEXT NOT NULL,
    open         NUMERIC(12,2),
    high         NUMERIC(12,2),
    low          NUMERIC(12,2),
    close        NUMERIC(12,2),
    volume       BIGINT,
    interval     TEXT            -- '1min' | '5min' | '1day'
);
SELECT create_hypertable('ohlcv', 'timestamp');

-- Per-strategy aggregated performance
CREATE TABLE strategy_performance (
    strategy        TEXT NOT NULL,
    period          TEXT NOT NULL,   -- 'all' | 'month' | 'week'
    total_trades    INTEGER,
    win_rate        NUMERIC(5,2),
    avg_rr          NUMERIC(6,2),
    total_pnl_inr   NUMERIC(14,2),
    max_drawdown    NUMERIC(5,2),
    sharpe          NUMERIC(6,2),
    last_updated    TIMESTAMPTZ,
    PRIMARY KEY (strategy, period)
);

-- Cross-agent shared memory (replaces Hermes file memory)
CREATE TABLE agent_memory (
    id           SERIAL PRIMARY KEY,
    key          TEXT UNIQUE NOT NULL,
    value        TEXT NOT NULL,
    source_agent TEXT,           -- 'data-agent' | 'strategy-agent' | 'hermes' etc
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    updated_at   TIMESTAMPTZ DEFAULT NOW(),
    expires_at   TIMESTAMPTZ     -- NULL = permanent
);

-- Backtest run results
CREATE TABLE backtest_results (
    id              SERIAL PRIMARY KEY,
    run_at          TIMESTAMPTZ DEFAULT NOW(),
    strategy        TEXT NOT NULL,
    from_date       DATE,
    to_date         DATE,
    symbols         TEXT[],
    total_trades    INTEGER,
    win_rate        NUMERIC(5,2),
    total_return_pct NUMERIC(8,2),
    max_drawdown    NUMERIC(5,2),
    sharpe          NUMERIC(6,2),
    notes           TEXT,
    approved        BOOLEAN DEFAULT FALSE  -- set to TRUE to allow paper trading
);
```

---

## Teams

### Team 1 — Dev Team

**Mission**: Build and maintain the trading application, bots, data pipelines, and research tools.

**How it works**:
1. Task received via Telegram or web chat
2. Hermes routes to Claude Code (`claude-code` skill) with `workdir=~/autotrade/dev/`
3. Claude Code multi-agent executes: writes code, runs tests, commits, pushes
4. Hermes reports completion back to Telegram

**Typical tasks**:
- Build new FastAPI endpoints
- Implement a new strategy module
- Write unit/integration tests
- Research and prototype new indicators
- Fix bugs, refactor, add DB migrations

**Claude Code flags**: `--max-turns 20`, full tools (Read, Edit, Write, Bash)

---

### Team 2 — Trading Desk

**Mission**: Run mock and eventually live strategies on NSE/BSE equities. Generate P&L. Build a track record.

**How it works**:
1. Triggered by Hermes cron (market schedule) OR on-demand via Telegram
2. Hermes routes to Claude Code with `workdir=~/autotrade/trading-desk/`
3. Claude Code spawns sub-agents:

```
Claude Code (orchestrator)
├── Data Agent      → fetch OHLCV from DB or live feed, compute indicators
├── Strategy Agent  → read validated strategies from DB, generate BUY/SELL/HOLD
├── Risk Agent      → check position limits, max daily loss, current drawdown
└── Execution Agent → write trade to DB (paper mode) / call broker API (live mode)
```

4. Every decision written to `trades` table with full reasoning trace
5. `agent_memory` updated with any new market observations
6. Hermes reads summary from DB, sends to Telegram

**Execution modes**: `paper` (default) → `live` (Phase 4, behind `EXECUTION_MODE` flag)

**Broker API target**: Zerodha Kite Connect (NSE/BSE)

#### Hermes Cron Schedule (IST)

| Time | Days | Action |
|------|------|--------|
| 09:10 | Mon–Fri | Pre-market scan — breadth, VIX, gap analysis |
| 09:20 | Mon–Fri | Intraday strategy run |
| 14:30 | Mon–Fri | Positional signal review |
| 15:20 | Mon–Fri | Close intraday positions |
| 16:00 | Mon–Fri | EOD P&L report → write to DB → push Git summary → Telegram |
| 18:00 | Mon–Fri | Backtesting run (off-market) |
| 10:00 | Sat–Sun | Weekend deep backtest + strategy review |

---

### Team 3 — Backtesting

**Mission**: Continuously validate and improve strategies using historical data. Off-market hours.

**How it works**:
1. Triggered by Hermes cron (evenings / weekends)
2. Claude Code reads historical OHLCV from TimescaleDB
3. Runs strategy backtest
4. Writes results to `backtest_results` table
5. Updates `strategy_performance` table
6. Sets `approved=TRUE` on strategies that meet thresholds
7. Writes one-page markdown summary → commits to Git
8. Updates `agent_memory` with key insights
9. Hermes notifies via Telegram

**Approval thresholds** (configurable):
- Win rate > 45%
- Sharpe > 0.8
- Max drawdown < 15%
- Min 30 trades in backtest period

---

## Repository Structure (code only)

```
autotrade/
├── PLAN.md
├── README.md
├── docker-compose.yml          ← PostgreSQL + TimescaleDB + Redis + app
├── .env.example
│
├── dev/                        ← Dev Team workspace
│   ├── api/                    # FastAPI backend
│   │   ├── main.py
│   │   ├── routers/
│   │   └── models/
│   ├── bots/                   # Bot source code
│   ├── research/               # Exploratory scripts
│   └── tests/
│
├── trading-desk/               ← Trading Desk workspace
│   ├── strategies/
│   │   ├── intraday/           # e.g. rsi_vwap, orb, vwap_bounce
│   │   ├── positional/         # e.g. momentum, mean_reversion
│   │   └── validated/          # Symlinks or copies of DB-approved strategies
│   ├── broker/
│   │   ├── zerodha.py          # Kite Connect integration
│   │   └── paper.py            # Paper trading engine
│   ├── data/
│   │   ├── nse.py              # NSE OHLCV fetcher
│   │   └── indicators.py       # Technical indicators
│   ├── risk/
│   │   ├── position_sizer.py
│   │   └── rules.yaml
│   ├── runner.py               # Main entry point
│   └── reports/                # Git-committed daily/backtest summaries
│       ├── daily/              # YYYY-MM-DD.md
│       └── backtest/           # <strategy>_<date>.md
│
├── shared/                     ← Shared across teams
│   ├── db.py                   # SQLAlchemy engine + session (shared DB connection)
│   ├── models.py               # Pydantic + SQLAlchemy models
│   ├── indicators.py           # Technical indicators library
│   ├── utils.py
│   └── config.py               # Centralised config from env vars
│
└── migrations/                 ← Alembic DB migrations
    └── versions/
```

---

## Infrastructure (Docker Compose)

```yaml
services:
  postgres:          # PostgreSQL 16 + TimescaleDB extension
  redis:             # Cache + message broker
  trading-app:       # FastAPI + Playwright/Chromium
```

All on the same GCP VM (e2-medium, 4GB RAM, 50GB disk).

---

## Hermes Configuration

### LLM
- **Model**: `qwen/qwen3-235b-a22b` via OpenRouter
- **Role**: Orchestration and routing only
- **Fallback**: `qwen/qwen-2.5-72b-instruct`

### Profiles

| Profile | SOUL.md focus | terminal.cwd |
|---------|--------------|--------------|
| `dev-team` | Build features, write tests, commit. Always run tests before committing. | `~/autotrade/dev/` |
| `trading-desk` | Execute strategies, manage risk, write full reasoning to DB on every decision | `~/autotrade/trading-desk/` |

### Claude Code Invocation per Team

**Dev Team**:
```
claude -p "<task>" --workdir ~/autotrade/dev/ --max-turns 20
```

**Trading Desk / Backtest**:
```
claude -p "<task>" --workdir ~/autotrade/trading-desk/ --max-turns 10
```

---

## Build Phases

### Phase 1 — Foundation
- [ ] Switch Hermes LLM → Qwen3-235b via OpenRouter
- [ ] Create `dev-team` and `trading-desk` Hermes profiles with SOUL.md
- [ ] Restructure repo: `dev/`, `trading-desk/`, `shared/`, `migrations/`
- [ ] Configure `terminal.cwd` per profile
- [ ] Set up Hermes cron placeholders (IST market schedule, disabled)

### Phase 2 — Data & Infrastructure
- [ ] Docker Compose: PostgreSQL 16 + TimescaleDB + Redis
- [ ] DB schema migrations (Alembic) — all tables above
- [ ] `shared/db.py` — SQLAlchemy engine shared by all agents
- [ ] NSE OHLCV data fetcher (`trading-desk/data/nse.py`)
- [ ] Shared indicators library (`shared/indicators.py`)
- [ ] Seed historical OHLCV into TimescaleDB (Nifty 50 stocks, 1-year)

### Phase 3 — Dev Team Pipeline
- [ ] FastAPI skeleton (`dev/api/`) with DB connection
- [ ] Paper trading engine (`trading-desk/broker/paper.py`) — writes to `trades` table
- [ ] Risk rules (`trading-desk/risk/rules.yaml` + `position_sizer.py`)
- [ ] Agent memory read/write helpers in `shared/db.py`

### Phase 4 — Trading Desk (Paper)
- [ ] First intraday strategy: RSI + VWAP (`trading-desk/strategies/intraday/rsi_vwap.py`)
- [ ] Backtesting framework — runs against TimescaleDB OHLCV
- [ ] Enable Hermes cron (market schedule)
- [ ] EOD report: query DB → generate markdown → commit → Telegram

### Phase 5 — Real Execution
- [ ] Zerodha Kite Connect integration (`trading-desk/broker/zerodha.py`)
- [ ] `EXECUTION_MODE=paper|live` flag wired end-to-end
- [ ] Enhanced risk controls: daily loss circuit breaker, max open positions
- [ ] Kill switch via Telegram: "stop all trading"

---

## Open Decisions

| Decision | Status | Notes |
|----------|--------|-------|
| OpenRouter API key | Pending | Need before Phase 1 LLM switch |
| Zerodha Kite Connect account | Future | Phase 5 real execution |
| Watchlist for intraday | TBD | Nifty 50 stocks? F&O stocks? |
| Strategy selection for Phase 4 | TBD | RSI+VWAP confirmed as first candidate |
| Telegram routing | TBD | One group with [dev]/[trade] prefix vs two separate groups |
| Historical data source | TBD | `nsepython`, `yfinance`, or NSE official API |
