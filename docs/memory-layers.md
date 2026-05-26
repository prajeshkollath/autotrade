# Memory Layers — Autotrade System

> How memory is stored, who owns it, and what persists across VM stop/start.
> Last updated: 2026-05-26

---

## Overview

The autotrade system uses four distinct memory layers. Each layer serves a different agent and a different purpose.

```
┌─────────────────────────────────────────────────────────────────┐
│                        HERMES (Orchestrator)                    │
│                                                                 │
│  ┌──────────────────┐   ┌──────────────────┐                   │
│  │  MEMORY.md       │   │  USER.md         │                   │
│  │  Agent notes     │   │  User profile    │                   │
│  │  ~800 tokens     │   │  ~500 tokens     │                   │
│  │  Injected every  │   │  Injected every  │                   │
│  │  session         │   │  session         │                   │
│  └──────────────────┘   └──────────────────┘                   │
│                                                                 │
│  ┌──────────────────┐   ┌──────────────────┐                   │
│  │  state.db        │   │  memory_store.db │                   │
│  │  Session history │   │  Holographic     │                   │
│  │  FTS5 search     │   │  Structured facts│                   │
│  │  All past chats  │   │  Trust scoring   │                   │
│  └──────────────────┘   └──────────────────┘                   │
└─────────────────────────────────────────────────────────────────┘
                              │
                              │ reads/writes via psql terminal call
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    PostgreSQL (Docker container)                 │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  agent_memory table                                      │   │
│  │  Cross-agent shared memory — the bridge                  │   │
│  │  Claude Code agents write here → Hermes reads back       │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                 │
│  (Phase 2 additions: trades, positions, ohlcv, backtest_results)│
└─────────────────────────────────────────────────────────────────┘
                              ▲
                              │ reads/writes via shared/db.py
                              │
┌─────────────────────────────────────────────────────────────────┐
│                   CLAUDE CODE (All agents)                      │
│                                                                 │
│  Dev Agent · Strategy Agent · Data Agent · Risk Agent          │
│  Execution Agent · Backtest Agent                              │
└─────────────────────────────────────────────────────────────────┘
```

---

## Layer 1 — Hermes Native Memory (Hermes-only)

| Store | File | What it holds | Size limit |
|-------|------|---------------|------------|
| Agent notes | `~/.hermes/memories/MEMORY.md` | Environment facts, conventions, things Hermes has learned | ~2,200 chars |
| User profile | `~/.hermes/memories/USER.md` | User preferences, communication style | ~1,375 chars |

**How it works**: Both files are injected into the system prompt at the start of every session. Hermes manages them itself via the `memory` tool (add/replace/remove).

**Persists across**: VM stop/start ✓ — VM deletion ✗

---

## Layer 2 — Hermes Session History (Hermes-only)

| Store | File | What it holds |
|-------|------|---------------|
| Session database | `~/.hermes/state.db` | Full conversation history, FTS5 full-text search across all past sessions |

**How it works**: Every Telegram message, web chat, and CLI session is stored here. Hermes can search past sessions with natural language queries.

**Persists across**: VM stop/start ✓ — VM deletion ✗

---

## Layer 3 — Holographic Fact Store (Hermes-only)

| Store | File | What it holds |
|-------|------|---------------|
| Holographic DB | `~/.hermes/memory_store.db` | Structured facts with trust scores, entity linking, HRR-based compositional retrieval |

**How it works**: Hermes uses the `fact_store` tool (9 actions: add, search, probe, related, reason, contradict, update, remove, list). Facts are auto-extracted at session end (`auto_extract: true`). Trust scores improve over time via `fact_feedback`.

**Persists across**: VM stop/start ✓ — VM deletion ✗

**Config** (`~/.hermes/config.yaml`):
```yaml
plugins:
  hermes-memory-store:
    db_path: "/home/freed/.hermes/memory_store.db"
    auto_extract: true
    default_trust: 0.6
```

---

## Layer 4 — PostgreSQL agent_memory (Shared — All Agents)

| Store | Where | What it holds |
|-------|-------|---------------|
| `agent_memory` table | PostgreSQL Docker container | Cross-session insights, strategy learnings, observations from all Claude Code agents |

**This is the bridge.** Hermes cannot read Claude Code's internal run context, and Claude Code agents cannot write to Hermes's SQLite stores. PostgreSQL is the neutral shared layer both sides can read and write.

**Who writes**: Any Claude Code agent (Dev, Strategy, Data, Risk, Execution, Backtest)
**Who reads**: All Claude Code agents on next run, Hermes (via `psql` terminal call)

**Schema**:
```sql
CREATE TABLE agent_memory (
    id           SERIAL PRIMARY KEY,
    key          TEXT UNIQUE NOT NULL,
    value        TEXT NOT NULL,
    source_agent TEXT,           -- 'data-agent' | 'strategy-agent' | 'hermes' etc
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    updated_at   TIMESTAMPTZ DEFAULT NOW(),
    expires_at   TIMESTAMPTZ     -- NULL = permanent
);
```

**Persists across**: VM stop/start ✓ — VM deletion ✗
**Backup**: GCP disk snapshots (configure separately)

---

## What Persists Where

| Scenario | Layers 1–3 (SQLite) | Layer 4 (PostgreSQL) |
|----------|---------------------|----------------------|
| VM restart / reboot | Safe | Safe |
| VM stop → start | Safe | Safe |
| VM deleted | Lost | Lost |
| Disaster recovery | GCP disk snapshot | GCP disk snapshot / pg_dump to GCS |

---

## Future Additions (Phase 2+)

When the full DB schema is deployed, PostgreSQL will also hold:

| Table | Purpose |
|-------|---------|
| `trades` | Every trade decision with full reasoning trace |
| `positions` | Current open positions |
| `ohlcv` | Market data (TimescaleDB hypertable) |
| `strategy_performance` | Aggregated stats per strategy |
| `backtest_results` | Backtest run history |

See [PLAN.md](../PLAN.md) for full schema.
