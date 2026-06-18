"""
ExpiryFlow -> NautilusTrader bridge.

Reads ExpiryFlow DuckDB and writes NT Parquet catalog:
  - OptionContract instruments
  - 5-min Bar data
  - OptionGreeks (delta/gamma/theta/vega + IV + spot) per bar

Usage:
    python convert.py [--underlying NIFTY] [--interval 5] [--db-path /tmp/snap.duckdb]
"""
import argparse
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
from nautilus_trader.model.data import OptionGreeks
from nautilus_trader.persistence.catalog import ParquetDataCatalog

from expiry_calendar import get_expiry_date
from instruments import make_option_contract
from bars import make_bar_type, make_bar
from greeks import compute_greeks

EXPIRYFLOW_DB = Path.home() / "ExpiryFlow/backend/options_data.duckdb"
CATALOG_PATH  = Path.home() / "autotrade/data/catalog"
IST = ZoneInfo("Asia/Kolkata")


def _ts_to_ns(ts_raw) -> int:
    if isinstance(ts_raw, datetime):
        dt = ts_raw if ts_raw.tzinfo else ts_raw.replace(tzinfo=IST)
    elif isinstance(ts_raw, str):
        dt = datetime.fromisoformat(ts_raw)
        if not dt.tzinfo:
            dt = dt.replace(tzinfo=IST)
    else:
        dt = datetime.fromtimestamp(float(ts_raw), tz=IST)
    return int(dt.astimezone(timezone.utc).timestamp() * 1e9)


def run(underlying: str = "NIFTY", interval: str = "5",
        limit: int | None = None, db_path: str | None = None):

    _db = db_path if db_path else str(EXPIRYFLOW_DB)
    print(f"Connecting to ExpiryFlow DuckDB: {_db}")
    conn = duckdb.connect(_db, read_only=True)

    query = f"""
        SELECT underlying_scrip, expiry_flag, expiry_code, interval,
               strike_label, strike_price, option_type,
               timestamp, open, high, low, close, volume, oi, iv, spot
        FROM expired_options_ohlcv
        WHERE underlying_scrip = '{underlying}'
          AND interval = '{interval}'
          AND strike_price IS NOT NULL
          AND open IS NOT NULL
        ORDER BY timestamp
    """
    if limit:
        query += f" LIMIT {limit}"

    print(f"Fetching {underlying} {interval}min data...")
    rows = conn.execute(query).fetchall()
    columns = [d[0] for d in conn.description]
    conn.close()

    print(f"Fetched {len(rows):,} rows. Building catalog...")
    catalog = ParquetDataCatalog(str(CATALOG_PATH))

    instruments_seen = {}
    bars_by_instrument = defaultdict(list)
    greeks_by_instrument = defaultdict(list)
    skipped_greeks = 0

    for row in rows:
        r = dict(zip(columns, row))
        ts_date = date.fromisoformat(str(r["timestamp"])[:10])

        try:
            expiry = get_expiry_date(
                ts_date, r["expiry_flag"], r["expiry_code"], r["underlying_scrip"]
            )
        except Exception:
            continue

        strike    = float(r["strike_price"])
        opt_type  = r["option_type"]
        inst_key  = (r["underlying_scrip"], expiry, strike, opt_type)

        if inst_key not in instruments_seen:
            instruments_seen[inst_key] = make_option_contract(
                underlying=r["underlying_scrip"],
                expiry=expiry,
                strike_price=strike,
                option_type=opt_type,
                ts_date=ts_date,
            )

        instrument = instruments_seen[inst_key]
        bar_type   = make_bar_type(instrument.id, r["interval"])

        bar = make_bar(bar_type, r)
        if bar:
            bars_by_instrument[instrument.id].append(bar)

        # Compute and store Greeks for every bar that has IV + spot
        iv   = r.get("iv")
        spot = r.get("spot")
        if iv and spot and float(iv) > 0 and float(spot) > 0:
            g = compute_greeks(
                spot=float(spot),
                strike=strike,
                iv=float(iv),
                ts_date=ts_date,
                expiry=expiry,
                option_type=opt_type,
            )
            if g:
                ts_ns = _ts_to_ns(r["timestamp"])
                og = OptionGreeks(
                    instrument.id,           # instrument_id
                    g["delta"],              # delta
                    g["gamma"],              # gamma
                    g["vega"],               # vega
                    g["theta"],              # theta
                    g["rho"],                # rho
                    float(iv),               # mark_iv
                    None,                    # bid_iv
                    None,                    # ask_iv
                    float(spot),             # underlying_price
                    float(r.get("oi", 0) or 0),  # open_interest
                    ts_ns,                   # ts_event
                    ts_ns,                   # ts_init
                )
                greeks_by_instrument[instrument.id].append(og)
            else:
                skipped_greeks += 1

    print(f"Writing {len(instruments_seen):,} instruments...")
    catalog.write_data(list(instruments_seen.values()))

    total_bars = 0
    for inst_id, bars in bars_by_instrument.items():
        catalog.write_data(bars)
        total_bars += len(bars)
    print(f"Bars written: {total_bars:,}")

    total_greeks = 0
    for inst_id, greeks_list in greeks_by_instrument.items():
        catalog.write_data(greeks_list)
        total_greeks += len(greeks_list)
    print(f"OptionGreeks written: {total_greeks:,}  (skipped {skipped_greeks:,} rows with no IV)")
    print(f"Done. Catalog at: {CATALOG_PATH}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--underlying", default="NIFTY")
    parser.add_argument("--interval", default="5")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--db-path", default=None, help="Override DuckDB path")
    args = parser.parse_args()
    run(args.underlying, args.interval, args.limit, args.db_path)
