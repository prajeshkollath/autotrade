"""
dhan_ohlcv_sync.py — Populate and maintain daily_ohlcv table from Yahoo Finance.

Uses yfinance (free, no auth) to download daily OHLCV for all NSE F&O stocks
and persists them to the daily_ohlcv PostgreSQL table.

The Dhan access token is NOT needed here. Dhan API can still be used for
live quotes / order flow where manual refresh is acceptable.

USAGE:
  # First run — backfill 1 year for all F&O stocks (~200 stocks, takes 2-3 min)
  cd ~/autotrade
  .venv/bin/python agents/dhan_ohlcv_sync.py --backfill

  # Daily incremental — fetch yesterday's close (run via cron after 6pm IST)
  .venv/bin/python agents/dhan_ohlcv_sync.py

  # Sync a specific symbol
  .venv/bin/python agents/dhan_ohlcv_sync.py --symbol RELIANCE

CRON (add to crontab on VM):
  30 18 * * 1-5  cd /home/freed/autotrade && HOME=/home/freed .venv/bin/python agents/dhan_ohlcv_sync.py >> /tmp/ohlcv_sync.log 2>&1
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import yfinance as yf

sys.path.insert(0, "/home/freed/autotrade")
from shared.db import (
    create_daily_ohlcv_table,
    get_ohlcv_latest_date,
    get_ohlcv_symbols,
    upsert_ohlcv_batch,
)

IST = timezone(timedelta(hours=5, minutes=30))

# All NSE F&O eligible stocks (as of 2026).
# Add/remove symbols here as NSE updates the F&O list quarterly.
FO_UNIVERSE: list[str] = [
    "ADANIENT","ADANIPORTS","ADANIPOWER","ADANITRANS","ALKEM","AMBUJACEM",
    "APOLLOHOSP","APOLLOTYRE","ASHOKLEY","ASIANPAINT","ASTRAL","ATUL",
    "AUBANK","AUROPHARMA","AXISBANK","BAJAJ-AUTO","BAJAJFINSV","BAJFINANCE",
    "BALKRISIND","BANDHANBNK","BANKBARODA","BEL","BERGEPAINT","BHARTIARTL",
    "BHEL","BIOCON","BOSCHLTD","BPCL","BRITANNIA","BSOFT","CANBK",
    "CANFINHOME","CDSL","CESC","CHOLAFIN","CIPLA","COALINDIA","COFORGE",
    "COLPAL","CONCOR","COROMANDEL","CROMPTON","CUMMINSIND","CYIENT",
    "DABUR","DALBHARAT","DEEPAKNTR","DELTACORP","DIVISLAB","DIXON",
    "DLF","DRREDDY","EICHERMOT","ESCORTS","EXIDEIND","FEDERALBNK",
    "FSL","GAIL","GLENMARK","GMRINFRA","GNFC","GODREJCP","GODREJPROP",
    "GRANULES","GRASIM","GSPL","HCLTECH","HDFCBANK","HDFCLIFE","HEROMOTOCO",
    "HINDALCO","HINDCOPPER","HINDPETRO","HINDUNILVR","HONAUT","IBULHSGFIN",
    "ICICIBank","ICICIBANK","ICICIGI","ICICIPRULI","IDBI","IDFCFIRSTB",
    "IEX","IGL","INDHOTEL","INDIAMART","INDIANB","INDIGO","INDUSINDBK",
    "INDUSTOWER","INFY","INTELLECT","IOC","IPCALAB","IRCTC","ITC",
    "JINDALSTEL","JKCEMENT","JSL","JSWENERGY","JSWSTEEL","JUBLFOOD",
    "KOTAKBANK","L&TFH","LALPATHLAB","LAURUSLABS","LICHSGFIN","LT",
    "LTIMINDTECH","LTTS","LUPIN","M&M","M&MFIN","MANAPPURAM","MARICO",
    "MARUTI","MCDOWELL-N","MCX","METROPOLIS","MFSL","MGL","MPHASIS",
    "MRF","MUTHOOTFIN","NATIONALUM","NAUKRI","NAVINFLUOR","NESTLEIND",
    "NMDC","NTPC","OBEROIRLTY","OFSS","ONGC","PAGEIND","PEL","PERSISTENT",
    "PETRONET","PFC","PIDILITIND","PIIND","PNB","POLYCAB","POWERGRID",
    "PVRINOX","RAIN","RAMCOCEM","RBLBANK","RECLTD","RELIANCE","ROUTE",
    "SAIL","SBICARD","SBILIFE","SBIN","SHREECEM","SHRIRAMFIN","SIEMENS",
    "SRF","STAR","SUNPHARMA","SUNTV","SYNGENE","TATACHEM","TATACOMM",
    "TATACONSUM","TATAMOTORS","TATAPOWER","TATASTEEL","TATATECH","TCS",
    "TECHM","TITAN","TORNTPHARM","TORNTPOWER","TRENT","TVSMOTOR",
    "UBL","ULTRACEMCO","UNIONBANK","UPL","VEDL","VOLTAS","WHIRLPOOL","WIPRO",
    "ZYDUSLIFE",
]


def _yf_symbol(sym: str) -> str:
    return sym + ".NS"


def _download_batch(symbols: list[str], start: str, end: str) -> dict[str, pd.DataFrame]:
    """
    Download daily OHLCV for a batch of symbols via yfinance.
    Returns {symbol: DataFrame} with columns Open/High/Low/Close/Volume.
    """
    yf_tickers = [_yf_symbol(s) for s in symbols]
    raw = yf.download(
        yf_tickers,
        start=start,
        end=end,
        interval="1d",
        auto_adjust=True,
        progress=False,
        threads=True,
    )

    result = {}
    if len(symbols) == 1:
        df = raw.dropna(subset=["Close"])
        if not df.empty:
            result[symbols[0]] = df
        return result

    for sym in symbols:
        col = _yf_symbol(sym)
        try:
            df = raw[col].dropna(subset=["Close"]) if col in raw.columns.get_level_values(1) else raw.xs(col, axis=1, level=1).dropna(subset=["Close"])
        except Exception:
            try:
                df = raw.xs(col, axis=1, level=1).dropna(subset=["Close"])
            except Exception:
                continue
        if not df.empty:
            result[sym] = df

    return result


def _to_db_rows(symbol: str, df: pd.DataFrame) -> list[dict]:
    rows = []
    for idx, row in df.iterrows():
        dt = idx.date() if hasattr(idx, "date") else idx
        rows.append({
            "symbol":     symbol,
            "trade_date": dt,
            "open":       round(float(row["Open"]),  2) if pd.notna(row.get("Open"))   else None,
            "high":       round(float(row["High"]),  2) if pd.notna(row.get("High"))   else None,
            "low":        round(float(row["Low"]),   2) if pd.notna(row.get("Low"))    else None,
            "close":      round(float(row["Close"]), 2) if pd.notna(row.get("Close"))  else None,
            "volume":     int(row["Volume"])              if pd.notna(row.get("Volume")) else 0,
        })
    return rows


def backfill(symbols: list[str], from_date: date, to_date: date, batch_size: int = 50):
    """Download 1 year of daily OHLCV for all F&O stocks in batches."""
    print(f"\n=== Backfill {from_date} → {to_date}  ({len(symbols)} symbols) ===")
    start = from_date.isoformat()
    end   = (to_date + timedelta(days=1)).isoformat()

    total_written = 0
    for i in range(0, len(symbols), batch_size):
        batch = symbols[i : i + batch_size]
        print(f"  Batch {i//batch_size + 1}: {batch[0]} … {batch[-1]}", end="", flush=True)
        try:
            data = _download_batch(batch, start, end)
            rows = []
            for sym, df in data.items():
                rows.extend(_to_db_rows(sym, df))
            if rows:
                upsert_ohlcv_batch(rows)
                total_written += len(rows)
            print(f"  → {len(rows)} rows")
        except Exception as exc:
            print(f"  ERROR: {exc}")

    print(f"\n  Total written: {total_written} rows")


def daily_sync(symbols: list[str]):
    """
    Incremental sync: for each symbol fetch from (latest_date+1) to yesterday.
    If no data exists yet for a symbol, falls back to 365-day backfill.
    """
    today     = datetime.now(IST).date()
    yesterday = today - timedelta(days=1)
    print(f"\n=== Daily sync (up to {yesterday}) ===")

    # Group symbols by their latest date to minimise API calls
    needs_full: list[str] = []
    needs_inc:  dict[date, list[str]] = {}

    for sym in symbols:
        latest = get_ohlcv_latest_date(sym)
        if latest is None:
            needs_full.append(sym)
        elif latest < yesterday:
            from_d = latest + timedelta(days=1)
            needs_inc.setdefault(from_d, []).append(sym)

    if needs_full:
        print(f"  {len(needs_full)} symbols with no data → full backfill")
        backfill(needs_full, today - timedelta(days=365), yesterday)

    total_written = 0
    for from_d, syms in sorted(needs_inc.items()):
        start = from_d.isoformat()
        end   = (yesterday + timedelta(days=1)).isoformat()
        print(f"  {len(syms)} symbols from {from_d}...", end="", flush=True)
        try:
            for i in range(0, len(syms), 50):
                batch = syms[i : i + 50]
                data  = _download_batch(batch, start, end)
                rows  = []
                for sym, df in data.items():
                    rows.extend(_to_db_rows(sym, df))
                if rows:
                    upsert_ohlcv_batch(rows)
                    total_written += len(rows)
            print(f" done")
        except Exception as exc:
            print(f" ERROR: {exc}")

    if not needs_full and not needs_inc:
        print("  All symbols already up to date")
    else:
        print(f"  Total written: {total_written} rows")


def main():
    ap = argparse.ArgumentParser(description="NSE F&O daily OHLCV sync (yfinance → PostgreSQL)")
    ap.add_argument("--backfill",   action="store_true", help="Full 1-year backfill for all symbols")
    ap.add_argument("--from-date",  default=None,        help="Backfill start YYYY-MM-DD")
    ap.add_argument("--to-date",    default=None,        help="Backfill end YYYY-MM-DD")
    ap.add_argument("--symbol",     default=None,        help="Sync a single symbol only")
    args = ap.parse_args()

    print(f"\n=== OHLCV Sync  {datetime.now(IST).strftime('%Y-%m-%d %H:%M IST')} ===")

    create_daily_ohlcv_table()
    print("  Table: daily_ohlcv ready")

    symbols = [args.symbol] if args.symbol else FO_UNIVERSE
    today   = datetime.now(IST).date()

    if args.backfill:
        from_date = date.fromisoformat(args.from_date) if args.from_date else today - timedelta(days=365)
        to_date   = date.fromisoformat(args.to_date)   if args.to_date  else today - timedelta(days=1)
        backfill(symbols, from_date, to_date)
    else:
        daily_sync(symbols)

    print("\n=== Done ===")


if __name__ == "__main__":
    main()
