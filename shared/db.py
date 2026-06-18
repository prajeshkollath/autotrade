"""
Shared database connection and agent_memory helpers.
All Claude Code agents import from here.
"""

import os
import psycopg2
import psycopg2.extras
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional
from pathlib import Path

# Load .env from repo root if present
_env_path = Path(__file__).resolve().parent.parent / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip())

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://autotrade:autotrade2026@localhost:5432/autotrade"
)


@contextmanager
def get_conn():
    conn = psycopg2.connect(DATABASE_URL)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def memory_set(key: str, value: str, source_agent: str, expires_at: Optional[datetime] = None) -> None:
    """Write or update a memory entry. Upserts on key."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO agent_memory (key, value, source_agent, expires_at)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (key) DO UPDATE SET
                    value = EXCLUDED.value,
                    source_agent = EXCLUDED.source_agent,
                    expires_at = EXCLUDED.expires_at,
                    updated_at = NOW()
            """, (key, value, source_agent, expires_at))


def memory_get(key: str) -> Optional[dict]:
    """Read a single memory entry by key. Returns None if not found or expired."""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT key, value, source_agent, created_at, updated_at, expires_at
                FROM agent_memory
                WHERE key = %s
                  AND (expires_at IS NULL OR expires_at > NOW())
            """, (key,))
            row = cur.fetchone()
            return dict(row) if row else None


def memory_list(source_agent: Optional[str] = None) -> list[dict]:
    """List all non-expired memory entries, optionally filtered by agent."""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if source_agent:
                cur.execute("""
                    SELECT key, value, source_agent, updated_at
                    FROM agent_memory
                    WHERE source_agent = %s
                      AND (expires_at IS NULL OR expires_at > NOW())
                    ORDER BY updated_at DESC
                """, (source_agent,))
            else:
                cur.execute("""
                    SELECT key, value, source_agent, updated_at
                    FROM agent_memory
                    WHERE expires_at IS NULL OR expires_at > NOW()
                    ORDER BY updated_at DESC
                """)
            return [dict(r) for r in cur.fetchall()]


def memory_delete(key: str) -> bool:
    """Delete a memory entry. Returns True if it existed."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM agent_memory WHERE key = %s", (key,))
            return cur.rowcount > 0


# ---------------------------------------------------------------------------
# daily_ohlcv helpers
# ---------------------------------------------------------------------------

def create_daily_ohlcv_table() -> None:
    """Create daily_ohlcv table and index if they don't exist."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS daily_ohlcv (
                    symbol      VARCHAR(20)  NOT NULL,
                    trade_date  DATE         NOT NULL,
                    open        NUMERIC(12,2),
                    high        NUMERIC(12,2),
                    low         NUMERIC(12,2),
                    close       NUMERIC(12,2),
                    volume      BIGINT,
                    PRIMARY KEY (symbol, trade_date)
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS daily_ohlcv_sym_date
                ON daily_ohlcv (symbol, trade_date DESC)
            """)


def upsert_ohlcv_batch(rows: list[dict]) -> int:
    """
    Insert or update OHLCV rows. Each row must have keys:
        symbol, trade_date, open, high, low, close, volume
    Returns number of rows written.
    """
    if not rows:
        return 0
    with get_conn() as conn:
        with conn.cursor() as cur:
            psycopg2.extras.execute_values(cur, """
                INSERT INTO daily_ohlcv (symbol, trade_date, open, high, low, close, volume)
                VALUES %s
                ON CONFLICT (symbol, trade_date) DO UPDATE SET
                    open   = EXCLUDED.open,
                    high   = EXCLUDED.high,
                    low    = EXCLUDED.low,
                    close  = EXCLUDED.close,
                    volume = EXCLUDED.volume
            """, [(r["symbol"], r["trade_date"], r["open"], r["high"],
                   r["low"], r["close"], r["volume"]) for r in rows])
    return len(rows)


def get_ohlcv_latest_date(symbol: str) -> Optional[datetime]:
    """Return the most recent trade_date for a symbol, or None."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT MAX(trade_date) FROM daily_ohlcv WHERE symbol = %s",
                (symbol,)
            )
            row = cur.fetchone()
            return row[0] if row and row[0] else None


def get_ohlcv_symbols() -> list[str]:
    """Return all distinct symbols in daily_ohlcv."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT symbol FROM daily_ohlcv ORDER BY symbol")
            return [r[0] for r in cur.fetchall()]


def get_ohlcv_df(symbol: str, days: int = 400):
    """
    Return daily OHLCV for a symbol as a list of dicts sorted by date ascending.
    Requires psycopg2 and returns raw dicts (no pandas dependency here).
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT trade_date, open, high, low, close, volume
                FROM daily_ohlcv
                WHERE symbol = %s
                ORDER BY trade_date ASC
                LIMIT %s
            """, (symbol, days))
            return [dict(r) for r in cur.fetchall()]
