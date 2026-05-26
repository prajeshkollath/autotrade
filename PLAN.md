# Autotrade — Architecture & Build Plan

> Living document. Updated as the project evolves.
> Last updated: 2026-05-26

---

## Vision

An autonomous trading system operated by two AI teams — a **Dev Team** and a **Trading Desk** — both orchestrated by Hermes and powered by Claude Code as the primary LLM engine.

---

## Guiding Principles

- **Hermes (Qwen) = thin router only** — receives instructions, delegates, reports back. No heavy reasoning.
- **Claude Code = the brain** — all development, all trading decisions, all backtesting. Maximises the Claude Pro subscription.
- **The repo is the shared memory** — code, strategies, logs, reports all live in Git. Agents "remember" by reading previous work.
- **Hermes memory = high-level insights** — cross-session learnings Hermes injects into future prompts (e.g. "RSI agent performs poorly on high-VIX days").
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
                         │
                   GIT REPO ◄──── single source of truth
              (code + strategies + logs + reports)
                         │
                 Hermes Memory ◄── high-level cross-session insights
```

### Memory Layers

| Layer | What it stores | Lifetime |
|-------|----------------|----------|
| **Hermes memory** | High-level insights, agent behaviour patterns, strategy performance summaries | Permanent, cross-session |
| **Git repo** | Code, strategies, backtest reports, trade logs, P&L | Permanent, versioned |
| **Claude Code run context** | Full reasoning within one task execution | Single run only |

---

## Teams

### Team 1 — Dev Team

**Mission**: Build and maintain the trading application, bots, data pipelines, and research tools.

**How it works**:
1. Task received via Telegram or web chat
2. Hermes routes to Claude Code (`claude-code` skill) with `workdir=~/autotrade/dev/`
3. Claude Code multi-agent executes: writes code, tests, commits, pushes
4. Hermes reports completion back to Telegram

**Typical tasks**:
- Build new FastAPI endpoints
- Implement a new trading strategy module
- Write unit/integration tests
- Research and prototype new indicators
- Fix bugs, refactor

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
├── Data Agent     → fetch OHLCV + indicators for target symbols
├── Strategy Agent → apply strategy logic → BUY / SELL / HOLD signal
├── Risk Agent     → check position limits, max daily loss, drawdown
└── Execution Agent → paper trade (now) / broker API (later)
```

4. Every decision written to structured log (full reasoning trace)
5. Hermes saves outcome summary to memory, sends Telegram notification

**Execution modes**: `paper` (default) → `live` (Phase 4, behind flag)

**Broker API target**: Zerodha Kite Connect (NSE/BSE)

#### Trade Decision Log (full reasoning trace)

```json
{
  "timestamp": "2026-05-26T09:20:00+05:30",
  "session_id": "trade_20260526_092000",
  "symbol": "RELIANCE",
  "strategy": "rsi_vwap_intraday",
  "market_context": {
    "nifty50_level": 22450,
    "india_vix": 14.2,
    "market_breadth": "bullish"
  },
  "signals": {
    "rsi_14": 42.3,
    "vwap_deviation_pct": -0.8,
    "volume_ratio_vs_avg": 1.3
  },
  "reasoning": "RSI at 42 = mild oversold. Price 0.8% below VWAP suggests mean reversion opportunity. Volume 30% above average confirms institutional interest.",
  "decision": "BUY",
  "confidence": 0.72,
  "entry_price": 2390,
  "stop_loss": 2370,
  "target": 2430,
  "risk_reward": 2.0,
  "position_size_shares": 10,
  "risk_amount_inr": 200,
  "execution_mode": "paper",
  "outcome": null
}
```

#### Hermes Cron Schedule (IST)

| Time | Trigger | Action |
|------|---------|--------|
| 09:10 | Pre-market | Scan watchlist, assess market breadth, VIX |
| 09:20 | Market open | Run intraday strategies |
| 14:30 | Mid-afternoon | Review positional signals |
| 15:20 | Pre-close | Close intraday positions |
| 16:00 | EOD | Generate P&L report → commit → Telegram |
| 18:00–20:00 | Off-market | Run backtests (weekdays) |

---

### Team 3 — Backtesting

**Mission**: Continuously validate and improve strategies using historical data. Off-market hours operation.

**How it works**:
1. Triggered by Hermes cron (evenings / weekends)
2. Claude Code fetches historical NSE OHLCV data
3. Runs strategy against historical data
4. Computes: total return, max drawdown, Sharpe ratio, win rate, avg R:R
5. Writes structured report to `trading-desk/reports/backtest/`
6. Updates `strategies/<name>/performance.json`
7. Commits + pushes → Hermes notifies via Telegram

**Backtest results feed into**:
- Strategy validation (which strategies are currently approved for paper trading)
- Risk calibration (position sizing based on historical drawdown)
- Hermes memory (high-level performance summaries)

---

## Repository Structure

```
autotrade/
├── PLAN.md                         ← this file
├── README.md
│
├── dev/                            ← Dev Team workspace
│   ├── api/                        # FastAPI backend
│   │   ├── main.py
│   │   ├── routers/
│   │   └── models/
│   ├── bots/                       # Trading bot source code
│   ├── research/                   # Notebooks, exploratory scripts
│   └── tests/
│
├── trading-desk/                   ← Trading Desk workspace
│   ├── strategies/
│   │   ├── intraday/               # e.g. rsi_vwap, orb, vwap_bounce
│   │   ├── positional/             # e.g. momentum, mean_reversion
│   │   └── validated/              # Strategies approved by backtest
│   ├── broker/                     # Broker API layer
│   │   ├── zerodha.py              # Kite Connect integration
│   │   └── paper.py                # Paper trading engine
│   ├── data/                       # Market data fetchers
│   │   ├── nse.py                  # NSE OHLCV + corporate actions
│   │   └── indicators.py           # Technical indicators
│   ├── risk/                       # Risk management
│   │   ├── position_sizer.py
│   │   └── rules.yaml              # Max loss, position limits
│   ├── runner.py                   # Main execution entry point
│   ├── logs/                       # Trade decision logs (gitignored)
│   └── reports/
│       ├── daily/                  # EOD P&L markdown (committed)
│       └── backtest/               # Backtest results (committed)
│
└── shared/                         ← Shared across teams
    ├── indicators.py               # Common technical indicators
    ├── models.py                   # Shared data models (Pydantic)
    ├── utils.py
    └── config.py                   # Centralised config (env vars)
```

---

## Hermes Configuration

### LLM
- **Model**: `qwen/qwen3-235b-a22b` via OpenRouter
- **Role**: Orchestration and routing only — no heavy reasoning
- **Fallback**: `qwen/qwen-2.5-72b-instruct` if 235B is unavailable

### Profiles

| Profile | SOUL.md focus | terminal.cwd |
|---------|--------------|--------------|
| `dev-team` | Build features, write tests, commit, never skip tests | `~/autotrade/dev/` |
| `trading-desk` | Execute strategies, manage risk, full reasoning trace on every decision | `~/autotrade/trading-desk/` |

### Claude Code Invocation per Team

**Dev Team**:
```
claude -p "<task>" --workdir ~/autotrade/dev/ --max-turns 20 \
  --allowedTools "Read,Edit,Write,Bash"
```

**Trading Desk**:
```
claude -p "<task>" --workdir ~/autotrade/trading-desk/ --max-turns 10 \
  --allowedTools "Read,Write,Bash"
```

---

## Build Phases

### Phase 1 — Foundation *(current)*
- [ ] Switch Hermes LLM → Qwen3-235b via OpenRouter
- [ ] Create `dev-team` and `trading-desk` Hermes profiles
- [ ] Restructure repo: `dev/`, `trading-desk/`, `shared/`
- [ ] Configure `terminal.cwd` per profile (one Hermes instance, profile switching)
- [ ] Set up Hermes cron placeholders (IST market schedule)

### Phase 2 — Dev Team Pipeline
- [ ] Claude Code builds FastAPI skeleton (`dev/api/`)
- [ ] NSE data fetcher (`trading-desk/data/nse.py`) — using `nsepython` or `yfinance`
- [ ] Shared technical indicators library (`shared/indicators.py`)
- [ ] Paper trading engine (`trading-desk/broker/paper.py`)
- [ ] Basic risk rules (`trading-desk/risk/rules.yaml`)

### Phase 3 — Trading Desk (Paper)
- [ ] First intraday strategy: RSI + VWAP (`trading-desk/strategies/intraday/rsi_vwap.py`)
- [ ] Backtesting framework (`trading-desk/`)
- [ ] Hermes cron: market open → run strategy → EOD report
- [ ] Full reasoning trace logging to JSON
- [ ] Daily P&L report auto-committed and sent to Telegram

### Phase 4 — Real Execution
- [ ] Zerodha Kite Connect integration (`trading-desk/broker/zerodha.py`)
- [ ] `EXECUTION_MODE=paper|live` flag wired through all agents
- [ ] Enhanced risk controls: max daily loss, circuit breakers
- [ ] Live trading with kill switch via Telegram

---

## Open Decisions

| Decision | Status | Notes |
|----------|--------|-------|
| OpenRouter API key | Pending | Need key before Phase 1 LLM switch |
| Zerodha Kite Connect account | Future | Needed for Phase 4 real execution |
| Watchlist for intraday | TBD | Nifty 50? Specific sectors? |
| Strategy selection for Phase 3 | TBD | RSI+VWAP confirmed as first candidate |
| Two Telegram groups vs one | TBD | One group with [dev]/[trade] prefix vs two groups |
