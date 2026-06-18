"""
dhan_fetcher.py — Dhan API client for equity OHLCV data.

Responsibilities:
  1. Download Dhan scrip master CSV → build symbol → security_id map for F&O stocks
  2. Fetch daily historical OHLCV via POST /v2/charts/historical

Environment variables required:
    DHAN_CLIENT_ID      — your Dhan client ID
    DHAN_ACCESS_TOKEN   — your Dhan access token (refreshes every 30 days)

Dhan API docs: https://dhanhq.co/docs/v2/historical-data/
"""
from __future__ import annotations

import csv
import io
import os
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import requests

DHAN_BASE   = "https://api.dhan.co/v2"
SCRIP_URL   = "https://images.dhan.co/api-data/api-scrip-master.csv"
CACHE_DIR   = Path("/home/freed/autotrade/data/dhan")
SCRIP_CACHE = CACHE_DIR / "scrip_master.csv"

IST = timezone(timedelta(hours=5, minutes=30))


def _auth_headers() -> dict:
    token     = os.environ.get("DHAN_ACCESS_TOKEN", "")
    client_id = os.environ.get("DHAN_CLIENT_ID", "")
    if not token or not client_id:
        raise RuntimeError(
            "DHAN_ACCESS_TOKEN and DHAN_CLIENT_ID must be set in environment / .env"
        )
    return {
        "access-token": token,
        "client-id":    client_id,
        "Content-Type": "application/json",
        "Accept":       "application/json",
    }


# ---------------------------------------------------------------------------
# Scrip master — F&O universe
# ---------------------------------------------------------------------------

def download_scrip_master(force: bool = False) -> Path:
    """
    Download Dhan scrip master CSV to local cache.
    Re-downloads only if cache is older than 1 day or force=True.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    if not force and SCRIP_CACHE.exists():
        age = datetime.now() - datetime.fromtimestamp(SCRIP_CACHE.stat().st_mtime)
        if age.total_seconds() < 86400:
            return SCRIP_CACHE

    print("  Downloading Dhan scrip master...", end="", flush=True)
    r = requests.get(SCRIP_URL, timeout=60)
    r.raise_for_status()
    SCRIP_CACHE.write_bytes(r.content)
    print(f" done ({len(r.content) // 1024} KB)")
    return SCRIP_CACHE


def build_fo_universe() -> dict[str, str]:
    """
    Parse scrip master and return {NSE_symbol: security_id} for all F&O eligible
    equity stocks (i.e., stocks that have futures listed on NSE-FO).

    Logic:
      - Find all FUTSTK rows on NSE_FNO → collect the trading symbols
      - Then find the corresponding NSE_EQ row for each symbol → get its security_id
      - That security_id is used to pull historical equity OHLCV

    Returns dict like {"RELIANCE": "2885", "TCS": "11536", ...}
    """
    csv_path = download_scrip_master()

    # Pass 1: collect symbols that have FUTSTK entries (= F&O eligible)
    fo_symbols: set[str] = set()
    eq_map: dict[str, str] = {}  # symbol → security_id for NSE EQ

    with open(csv_path, encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            exch = row.get("SEM_EXM_EXCH_ID", "").strip().upper()
            inst = row.get("SEM_INSTRUMENT_NAME", "").strip().upper()
            sym  = row.get("SEM_TRADING_SYMBOL", "").strip().upper()
            sid  = row.get("SEM_SMST_SECURITY_ID", "").strip()

            if exch == "NSE" and inst in ("FUTSTK",):
                # Strip expiry suffix — FUTSTK symbols look like "RELIANCE-FEB2026-FUT"
                base = sym.split("-")[0].split(" ")[0]
                fo_symbols.add(base)

            if exch == "NSE" and inst == "EQUITY":
                eq_map[sym] = sid

    # Pass 2: intersect
    universe = {
        sym: eq_map[sym]
        for sym in fo_symbols
        if sym in eq_map
    }

    print(f"  F&O universe: {len(universe)} stocks")
    return universe


# ---------------------------------------------------------------------------
# Historical OHLCV fetch
# ---------------------------------------------------------------------------

def fetch_historical(
    security_id: str,
    from_date: date,
    to_date: date,
    exchange_segment: str = "NSE_EQ",
    instrument: str = "EQUITY",
) -> list[dict]:
    """
    Call Dhan POST /v2/charts/historical and return list of OHLCV dicts.

    Each dict: {date: date, open, high, low, close: float, volume: int}

    Dhan returns parallel arrays: open[], high[], low[], close[], volume[], timestamp[]
    timestamp values are Unix epoch seconds (IST).
    """
    url = f"{DHAN_BASE}/charts/historical"
    payload = {
        "securityId":      security_id,
        "exchangeSegment": exchange_segment,
        "instrument":      instrument,
        "expiryCode":      0,
        "oi":              False,
        "fromDate":        from_date.strftime("%Y-%m-%d"),
        "toDate":          to_date.strftime("%Y-%m-%d"),
    }

    resp = requests.post(url, json=payload, headers=_auth_headers(), timeout=30)

    if resp.status_code == 429:
        # Rate limited — back off
        time.sleep(2)
        resp = requests.post(url, json=payload, headers=_auth_headers(), timeout=30)

    if resp.status_code != 200:
        raise RuntimeError(
            f"Dhan API error {resp.status_code} for security_id={security_id}: {resp.text[:200]}"
        )

    data = resp.json()

    opens      = data.get("open",      [])
    highs      = data.get("high",      [])
    lows       = data.get("low",       [])
    closes     = data.get("close",     [])
    volumes    = data.get("volume",    [])
    timestamps = data.get("timestamp", [])

    if not timestamps:
        return []

    rows = []
    for i, ts in enumerate(timestamps):
        try:
            # Dhan timestamps are Unix epoch seconds
            dt = datetime.fromtimestamp(int(ts), tz=IST).date()
            rows.append({
                "date":   dt,
                "open":   float(opens[i])   if i < len(opens)   else None,
                "high":   float(highs[i])   if i < len(highs)   else None,
                "low":    float(lows[i])    if i < len(lows)    else None,
                "close":  float(closes[i])  if i < len(closes)  else None,
                "volume": int(volumes[i])   if i < len(volumes) else 0,
            })
        except (ValueError, IndexError):
            continue

    return rows


def fetch_historical_chunked(
    security_id: str,
    from_date: date,
    to_date: date,
    chunk_days: int = 365,
    delay: float = 0.25,
) -> list[dict]:
    """
    Fetch historical data in yearly chunks (Dhan limits range per request).
    Adds a small delay between chunks to avoid rate limiting.
    """
    all_rows: list[dict] = []
    cursor = from_date

    while cursor <= to_date:
        chunk_end = min(cursor + timedelta(days=chunk_days - 1), to_date)
        rows = fetch_historical(security_id, cursor, chunk_end)
        all_rows.extend(rows)
        cursor = chunk_end + timedelta(days=1)
        if cursor <= to_date:
            time.sleep(delay)

    # Deduplicate by date (keep last)
    seen: dict[date, dict] = {}
    for r in all_rows:
        seen[r["date"]] = r
    return sorted(seen.values(), key=lambda r: r["date"])
