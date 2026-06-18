"""
dhan_ohlcv_sync.py — Populate and maintain daily_ohlcv table from Dhan API.

USAGE:
  # First run — backfill 1 year for all F&O stocks (takes ~5-10 min)
  cd ~/autotrade
  .venv/bin/python agents/dhan_ohlcv_sync.py --backfill

  # Daily incremental — fetch yesterday's close (run via cron after 6pm IST)
  .venv/bin/python agents/dhan_ohlcv_sync.py

  # Sync a specific symbol only
  .venv/bin/python agents/dhan_ohlcv_sync.py --symbol RELIANCE

  # Force full re-download of scrip master + rebuild universe
  .venv/bin/python agents/dhan_ohlcv_sync.py --backfill --refresh-scrip

CRON (add to crontab on VM):
  30 18 * * 1-5  cd /home/freed/autotrade && HOME=/home/freed .venv/bin/python agents/dhan_ohlcv_sync.py >> /tmp/ohlcv_sync.log 2>&1

ENV (in ~/autotrade/.env):
  DHAN_CLIENT_ID=1105374361
  DHAN_ACCESS_TOKEN=<token>
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, "/home/freed/autotrade")

from agents.dhan_fetcher import (
    build_fo_universe,
    download_scrip_master,
    fetch_historical_chunked,
)
from shared.db import (
    create_daily_ohlcv_table,
    get_ohlcv_latest_date,
    get_ohlcv_symbols,
    upsert_ohlcv_batch,
)

IST          = timezone(timedelta(hours=5, minutes=30))
UNIVERSE_PATH = Path("/home/freed/autotrade/data/dhan/fo_universe.json")


# ---------------------------------------------------------------------------
# Universe helpers
# ---------------------------------------------------------------------------

def load_or_build_universe(force_refresh: bool = False) -> dict[str, str]:
    """
    Load cached F&O universe from JSON, or rebuild from scrip master.
    Returns {symbol: security_id}.
    """
    if not force_refresh and UNIVERSE_PATH.exists():
        u = json.loads(UNIVERSE_PATH.read_text())
        print(f"  Universe loaded from cache: {len(u)} stocks")
        return u

    download_scrip_master(force=force_refresh)
    u = build_fo_universe()

    UNIVERSE_PATH.parent.mkdir(parents=True, exist_ok=True)
    UNIVERSE_PATH.write_text(json.dumps(u, indent=2))
    print(f"  Universe saved → {UNIVERSE_PATH}")
    return u


# ---------------------------------------------------------------------------
# Sync helpers
# ---------------------------------------------------------------------------

def _rows_for_db(symbol: str, api_rows: list[dict]) -> list[dict]:
    return [
        {
            "symbol":     symbol,
            "trade_date": r["date"],
            "open":       r["open"],
            "high":       r["high"],
            "low":        r["low"],
            "close":      r["close"],
            "volume":     r["volume"],
        }
        for r in api_rows
    ]


def backfill(
    universe: dict[str, str],
    from_date: date,
    to_date: date,
    symbol_filter: str | None = None,
    delay: float = 0.3,
):
    """
    Backfill daily OHLCV for all F&O stocks from from_date to to_date.
    Skips symbols that already have data up to (to_date - 2 days).
    """
    print(f"\n=== Backfill {from_date} → {to_date} ===")
    symbols = (
        {symbol_filter: universe[symbol_filter]}
        if symbol_filter and symbol_filter in universe
        else universe
    )
    total   = len(symbols)
    written = 0
    skipped = 0
    errors  = 0

    for i, (sym, sid) in enumerate(symbols.items(), 1):
        # Skip if already up to date
        latest = get_ohlcv_latest_date(sym)
        if latest and latest >= (to_date - timedelta(days=3)):
            skipped += 1
            continue

        try:
            rows = fetch_historical_chunked(sid, from_date, to_date)
            if rows:
                n = upsert_ohlcv_batch(_rows_for_db(sym, rows))
                written += n
                print(f"  [{i:3d}/{total}] {sym:<20} {n:4d} rows   latest={rows[-1]['date']}")
            else:
                print(f"  [{i:3d}/{total}] {sym:<20} no data returned")
        except Exception as exc:
            errors += 1
            print(f"  [{i:3d}/{total}] {sym:<20} ERROR: {exc}")

        time.sleep(delay)

    print(f"\n  Done — written {written} rows | skipped {skipped} | errors {errors}")


def daily_sync(
    universe: dict[str, str],
    symbol_filter: str | None = None,
    delay: float = 0.2,
):
    """
    Incremental sync: for each stock fetch from (latest_date + 1) to yesterday.
    If a stock has no data at all, falls back to a 365-day backfill.
    """
    today     = datetime.now(IST).date()
    yesterday = today - timedelta(days=1)

    print(f"\n=== Daily sync (up to {yesterday}) ===")

    symbols = (
        {symbol_filter: universe[symbol_filter]}
        if symbol_filter and symbol_filter in universe
        else universe
    )
    total   = len(symbols)
    written = 0
    skipped = 0
    errors  = 0

    for i, (sym, sid) in enumerate(symbols.items(), 1):
        latest = get_ohlcv_latest_date(sym)

        if latest and latest >= yesterday:
            skipped += 1
            continue

        from_date = (latest + timedelta(days=1)) if latest else (today - timedelta(days=365))

        try:
            rows = fetch_historical_chunked(sid, from_date, yesterday)
            if rows:
                n = upsert_ohlcv_batch(_rows_for_db(sym, rows))
                written += n
                print(f"  [{i:3d}/{total}] {sym:<20} +{n:3d} rows   up to {rows[-1]['date']}")
            else:
                skipped += 1
        except Exception as exc:
            errors += 1
            print(f"  [{i:3d}/{total}] {sym:<20} ERROR: {exc}")

        time.sleep(delay)

    print(f"\n  Done — written {written} rows | skipped {skipped} | errors {errors}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Dhan OHLCV sync for F&O universe")
    ap.add_argument("--backfill",      action="store_true",
                    help="Backfill 1 year of data for all F&O stocks")
    ap.add_argument("--from-date",     default=None,
                    help="Override backfill start date YYYY-MM-DD (default: 1 year ago)")
    ap.add_argument("--to-date",       default=None,
                    help="Override backfill end date YYYY-MM-DD (default: yesterday)")
    ap.add_argument("--symbol",        default=None,
                    help="Sync a single symbol only (e.g. RELIANCE)")
    ap.add_argument("--refresh-scrip", action="store_true",
                    help="Force re-download of Dhan scrip master CSV")
    args = ap.parse_args()

    print("\n=== Dhan OHLCV Sync ===")
    print(f"  Time: {datetime.now(IST).strftime('%Y-%m-%d %H:%M IST')}")

    # Ensure table exists
    create_daily_ohlcv_table()
    print("  Table: daily_ohlcv ready")

    # Load universe
    universe = load_or_build_universe(force_refresh=args.refresh_scrip)
    if not universe:
        print("ERROR: empty universe — check Dhan scrip master")
        sys.exit(1)

    today     = datetime.now(IST).date()
    yesterday = today - timedelta(days=1)

    if args.backfill:
        from_date = date.fromisoformat(args.from_date) if args.from_date else today - timedelta(days=365)
        to_date   = date.fromisoformat(args.to_date)   if args.to_date  else yesterday
        backfill(universe, from_date, to_date, symbol_filter=args.symbol)
    else:
        daily_sync(universe, symbol_filter=args.symbol)

    print("\n=== Sync complete ===")


if __name__ == "__main__":
    main()
