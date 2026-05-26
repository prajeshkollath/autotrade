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
