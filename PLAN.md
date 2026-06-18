# Autotrade — Build Plan

> Last updated: 2026-06-18
> Full architecture: [docs/architecture.md](docs/architecture.md)

---

## What's Built and Working

### Infrastructure
- [x] GCP VM (e2-medium, 50GB, us-central1-a, IP 34.45.46.60)
- [x] OpenAlgo running at localhost:5000 — broker bridge to Zerodha
- [x] ExpiryFlow running — options chain, greeks, expiry calendar
- [x] PostgreSQL 16 in Docker — `agent_memory` + `task_tokens` tables live
- [x] Claude Code CLI on VM — OAuth auto-refresh via Secret Manager + daily cron
- [x] Web dashboard (Flask) at port 8080 — Trading / Screener / Strategies views
- [x] GitHub repo synced — all code committed, VM uses deploy key for push

### Trading Pipeline
- [x] Morning brief (6am) — TradingAgents (GPT-4o) + OI Analyst → morning_brief.json
- [x] OI Analyst — PCR, max pain, OI walls, expected range, strategy recommendation
- [x] Strategy entry — `start_strategy.py` reads strategies.json → places CE+PE sells
- [x] Position manager loop (every min) — GPT-4o decides HOLD/ADJUST/EXIT → OpenAlgo
- [x] Decision executor — all action types: HOLD, SHIFT_STRIKE, PARTIAL_EXIT, FULL_EXIT, ADD_POSITION, HEDGE_DELTA
- [x] Decision logger — every decision to data/decision_logs/ JSONL
- [x] Post-market — EOD P&L summary
- [x] Backtest replay — historical day replayed through live agent pipeline via NT Parquet catalog

### Active Strategies (sandbox mode)
- [x] NIFTY Short Strangle (NFO options)
- [x] GOLDM Short Strangle (MCX options)
- [x] ADANIENT, HINDALCO, ADANIPORTS equity long

### Adapters
- [x] `adapters/openalgo/` — full Nautilus Trader LiveDataClient + LiveExecutionClient
- [x] `adapters/expiryflow_bridge/` — options data (bars, greeks, instruments, expiry)

---

## What's Next

### Immediate
- [ ] Wire `ANTHROPIC_API_KEY` into position_manager — swap GPT-4o decisions to Claude Sonnet
- [ ] `trades` table in PostgreSQL — log every decision with full reasoning (currently only JSONL files)
- [ ] EOD report → write to DB → commit markdown summary to git → Telegram notification
- [ ] `strategies.json` into the git repo (currently in `data/` which is gitignored)

### Data & Infrastructure
- [ ] TimescaleDB upgrade for OHLCV hypertable (current: Parquet catalog only)
- [ ] Seed historical OHLCV into TimescaleDB for faster backtesting
- [ ] Redis for intraday signal caching and pub/sub between agents

### Backtesting
- [ ] VectorBT strategy runner wired to existing strategy files
- [ ] Backtest approval gate — strategies must pass win rate / Sharpe / drawdown thresholds before going live

### Live Trading
- [ ] `EXECUTION_MODE=live` full end-to-end test with small position
- [ ] Zerodha kill switch via Telegram — "stop all trading"
- [ ] Daily loss circuit breaker in position_manager

### Scheduling (replace manual runs)
- [ ] 6am cron → morning_brief.py
- [ ] 9:15am cron → start_strategy.py for each active strategy
- [ ] 3:30pm cron → post_market.py
- [ ] All triggered via Claude Code on schedule
