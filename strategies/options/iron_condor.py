"""
Iron Condor Strategy — NIFTY Weekly Options
============================================
Sells an OTM strangle + buys a further-out strangle for protection.

  SELL  ATM + short_offset  CE
  SELL  ATM - short_offset  PE
  BUY   ATM + short_offset + wing_width  CE
  BUY   ATM - short_offset - wing_width  PE

Entry  : 09:20 IST on Monday, nearest Thursday expiry
Exit   : 15:20 IST (EOD) or loss > stop_loss_multiple × credit received
Greeks : Computed from DuckDB snapshot at entry and exit

HOW TO RUN:
  cd ~/autotrade
  .venv/bin/python strategies/options/iron_condor.py
  .venv/bin/python strategies/options/iron_condor.py --from 2025-09-01 --to 2026-06-03
"""
import argparse
import shutil
import sys
from datetime import date, datetime, timedelta, time, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, "/home/freed/autotrade")
sys.path.insert(0, "/home/freed/autotrade/adapters/expiryflow_bridge")

import duckdb
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.config import BacktestEngineConfig, StrategyConfig
from nautilus_trader.model.currencies import INR
from nautilus_trader.model.data import Bar, BarSpecification, BarType
from nautilus_trader.model.enums import (
    AccountType, AggregationSource, BarAggregation,
    OmsType, OrderSide, PriceType,
)
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.model.instruments import OptionContract
from nautilus_trader.model.objects import Money, Quantity
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from nautilus_trader.trading.strategy import Strategy

from greeks import compute_greeks

CATALOG_PATH  = Path.home() / "autotrade/data/catalog"
EXPIRYFLOW_DB = Path.home() / "ExpiryFlow/backend/options_data.duckdb"
IST           = ZoneInfo("Asia/Kolkata")
NSE           = Venue("NSE")
ENTRY_TIME    = time(9, 20)
EXIT_TIME     = time(15, 20)
STRIKE_STEP   = 50


# ---------------------------------------------------------------------------
# Module-level helpers (used by both strategy and run_backtest)
# ---------------------------------------------------------------------------

def _next_expiry(from_date: date) -> date:
    """Next Thursday on or after from_date."""
    days = (3 - from_date.weekday()) % 7
    return from_date + timedelta(days=days)


def _make_inst_id(expiry: date, strike: float, opt_type: str) -> InstrumentId:
    return InstrumentId(
        Symbol(f"NIFTY_{expiry.strftime('%Y%m%d')}_{int(strike)}_{opt_type}"),
        NSE,
    )


def _get_needed_instrument_ids(
    from_date: date, to_date: date, db_path: str,
    short_offset: int = 2, wing_width: int = 2,
) -> set:
    """
    Query DuckDB for Monday spot prices in the date range.
    Returns the set of InstrumentIds the strategy will actually trade —
    only 4 per week instead of all 40+ per expiry.
    """
    # Build list of Mondays in range
    mondays = []
    d = from_date
    while d <= to_date:
        if d.weekday() == 0:
            mondays.append(d.isoformat())
        d += timedelta(days=1)

    if not mondays:
        return set()

    conn = duckdb.connect(db_path, read_only=True)
    placeholders = ",".join(["?" for _ in mondays])
    rows = conn.execute(f"""
        SELECT timestamp::DATE as trade_date, AVG(spot) as avg_spot
        FROM expired_options_ohlcv
        WHERE underlying_scrip = 'NIFTY'
          AND timestamp::DATE IN ({placeholders})
          AND TIME(timestamp) BETWEEN '09:15:00' AND '09:25:00'
          AND spot > 0
        GROUP BY trade_date
        ORDER BY trade_date
    """, mondays).fetchall()
    conn.close()

    ids = set()
    for trade_date, spot in rows:
        if isinstance(trade_date, str):
            trade_date = date.fromisoformat(trade_date)
        atm    = round(float(spot) / STRIKE_STEP) * STRIKE_STEP
        expiry = _next_expiry(trade_date)
        for offset, opt_type in [
            ( short_offset,              "CE"),
            (-short_offset,              "PE"),
            ( short_offset + wing_width, "CE"),
            (-(short_offset + wing_width), "PE"),
        ]:
            ids.add(_make_inst_id(expiry, atm + offset * STRIKE_STEP, opt_type))

    return ids


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

class IronCondorConfig(StrategyConfig, frozen=True):
    underlying: str           = "NIFTY"
    short_strike_offset: int  = 2
    wing_width: int           = 2
    lots: int                 = 1
    stop_loss_multiple: float = 2.0
    entry_weekday: int        = 0      # 0 = Monday
    db_path: str              = "/tmp/ic_db_snap.duckdb"


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------

class IronCondor(Strategy):

    def __init__(self, config: IronCondorConfig):
        super().__init__(config)
        self._in_position: bool             = False
        self._entry_credit: float           = 0.0
        self._leg_ids: list                 = []
        self._trades: list                  = []
        self._entry_date: date | None       = None
        self._last_entry_attempt: date | None = None

    def on_start(self):
        for inst_id in self.cache.instrument_ids():
            if not isinstance(self.cache.instrument(inst_id), OptionContract):
                continue
            self.subscribe_bars(BarType(
                instrument_id=inst_id,
                bar_spec=BarSpecification(5, BarAggregation.MINUTE, PriceType.LAST),
                aggregation_source=AggregationSource.EXTERNAL,
            ))
        self.log.info("IronCondor ready")

    def on_bar(self, bar: Bar):
        dt_ist     = datetime.fromtimestamp(bar.ts_event / 1e9, tz=timezone.utc).astimezone(IST)
        bar_time   = dt_ist.time()
        entry_date = dt_ist.date()

        if (dt_ist.weekday() == self.config.entry_weekday
                and bar_time == ENTRY_TIME
                and not self._in_position
                and entry_date != self._last_entry_attempt):
            self._last_entry_attempt = entry_date
            self._enter(entry_date)

        if self._in_position:
            if bar_time >= EXIT_TIME:
                self._exit("EOD", dt_ist)
                return
            self._check_stop_loss()

    def _get_spot(self, ts_date: date) -> float | None:
        try:
            conn = duckdb.connect(self.config.db_path, read_only=True)
            row  = conn.execute("""
                SELECT spot FROM expired_options_ohlcv
                WHERE underlying_scrip = 'NIFTY'
                  AND timestamp::DATE = ?
                  AND spot > 0
                ORDER BY timestamp LIMIT 1
            """, [ts_date.isoformat()]).fetchone()
            conn.close()
            return float(row[0]) if row else None
        except Exception:
            return None

    def _get_greeks(self, ts_date: date, expiry: date,
                    strike: float, opt_type: str) -> dict | None:
        try:
            conn = duckdb.connect(self.config.db_path, read_only=True)
            row  = conn.execute("""
                SELECT iv, spot FROM expired_options_ohlcv
                WHERE underlying_scrip = 'NIFTY'
                  AND timestamp::DATE = ?
                  AND strike_price = ?
                  AND option_type = ?
                  AND iv > 0
                ORDER BY timestamp LIMIT 1
            """, [ts_date.isoformat(), strike, opt_type]).fetchone()
            conn.close()
            if not row:
                return None
            return compute_greeks(float(row[1]), strike, float(row[0]),
                                  ts_date, expiry, opt_type)
        except Exception:
            return None

    def _enter(self, entry_date: date):
        spot = self._get_spot(entry_date)
        if spot is None:
            self.log.warning(f"No spot for {entry_date} — skipping")
            return

        expiry = _next_expiry(entry_date)
        atm    = round(spot / STRIKE_STEP) * STRIKE_STEP
        cfg    = self.config

        legs = [
            (atm + cfg.short_strike_offset * STRIKE_STEP,              "CE", OrderSide.SELL),
            (atm - cfg.short_strike_offset * STRIKE_STEP,              "PE", OrderSide.SELL),
            (atm + (cfg.short_strike_offset + cfg.wing_width) * STRIKE_STEP, "CE", OrderSide.BUY),
            (atm - (cfg.short_strike_offset + cfg.wing_width) * STRIKE_STEP, "PE", OrderSide.BUY),
        ]

        for strike, opt_type, side in legs:
            inst_id = _make_inst_id(expiry, strike, opt_type)
            inst    = self.cache.instrument(inst_id)
            if inst is None:
                self.log.warning(f"Not in cache: {inst_id} — abort")
                return
            order = self.order_factory.market(inst_id, side, inst.make_qty(Decimal(str(cfg.lots))))
            self.submit_order(order)
            self._leg_ids.append(inst_id)

        self._in_position = True
        self._entry_date  = entry_date
        self._entry_credit = 0.0
        self.log.info(f"IC ENTRY | {entry_date} | expiry={expiry} | ATM={atm} | spot={spot:.0f}")

        for strike, opt_type, side in legs:
            g = self._get_greeks(entry_date, expiry, strike, opt_type)
            if g:
                self.log.info(
                    f"  {side.name:4} {int(strike)}{opt_type}: "
                    f"delta={g['delta']:+.3f} theta={g['theta']:+.2f} "
                    f"vega={g['vega']:.2f} iv={g['iv']:.1f}%"
                )

    def _check_stop_loss(self):
        if not self._entry_credit:
            return
        total_pnl = sum(
            self.portfolio.unrealized_pnl(i).as_double()
            for i in self._leg_ids
        )
        if total_pnl < -(self.config.stop_loss_multiple * self._entry_credit):
            self._exit("STOP_LOSS", datetime.now(tz=IST))

    def _exit(self, reason: str, dt_ist: datetime):
        exit_date = dt_ist.date()
        for inst_id in self._leg_ids:
            self.close_all_positions(inst_id)

        if self._entry_date:
            expiry = _next_expiry(self._entry_date)
            self.log.info(f"IC EXIT | reason={reason} | credit={self._entry_credit:.2f}")
            for inst_id in self._leg_ids:
                parts = str(inst_id).split(".")[0].split("_")
                if len(parts) == 4:
                    g = self._get_greeks(exit_date, expiry, float(parts[2]), parts[3])
                    if g:
                        self.log.info(
                            f"  exit {parts[2]}{parts[3]}: "
                            f"delta={g['delta']:+.3f} theta={g['theta']:+.2f} iv={g['iv']:.1f}%"
                        )

        self._trades.append({
            "entry_date": str(self._entry_date),
            "exit_reason": reason,
            "credit": self._entry_credit,
        })
        self._in_position  = False
        self._leg_ids      = []
        self._entry_date   = None

    def on_order_filled(self, event):
        inst = self.cache.instrument(event.instrument_id)
        if inst is None:
            return
        px   = float(event.last_px)
        qty  = float(event.last_qty)
        if event.order_side == OrderSide.SELL:
            self._entry_credit += px * qty
        else:
            self._entry_credit -= px * qty

    def on_stop(self):
        if self._in_position:
            self._exit("STRATEGY_STOP", datetime.now(tz=IST))
        self._print_summary()

    def _print_summary(self):
        print(f"\n=== IRON CONDOR SUMMARY — {len(self._trades)} trades ===")
        total = sum(t["credit"] for t in self._trades)
        stops = sum(1 for t in self._trades if t["exit_reason"] == "STOP_LOSS")
        print(f"  Total credit collected : {total:>10.2f}")
        print(f"  Stop-loss exits        : {stops}/{len(self._trades)}")
        for i, t in enumerate(self._trades, 1):
            print(f"  {i:3d}. {t['entry_date']}  credit={t['credit']:8.2f}  exit={t['exit_reason']}")


# ---------------------------------------------------------------------------
# Backtest runner
# ---------------------------------------------------------------------------

def run_backtest(from_date: date, to_date: date,
                 short_offset: int = 2, wing_width: int = 2,
                 lots: int = 1, stop_loss: float = 2.0):

    # 1. Snapshot live DB (avoids write-lock conflict)
    snap_path = "/tmp/ic_db_snap.duckdb"
    shutil.copy2(str(EXPIRYFLOW_DB), snap_path)
    print(f"DB snapshot: {snap_path}")

    # 2. Pre-select ONLY the 4 leg instruments per Monday — fast load
    needed_ids = _get_needed_instrument_ids(
        from_date, to_date, snap_path, short_offset, wing_width
    )
    print(f"Pre-selected {len(needed_ids)} instruments for {from_date} → {to_date}")

    # 3. Load only those from catalog
    catalog  = ParquetDataCatalog(str(CATALOG_PATH))
    all_insts = catalog.instruments(instrument_type=OptionContract)
    relevant  = [i for i in all_insts if i.id in needed_ids]

    if not relevant:
        print("No matching instruments found in catalog. Run bridge first.")
        return

    print(f"Matched {len(relevant)} instruments in catalog")

    # 4. Build engine
    engine = BacktestEngine(config=BacktestEngineConfig(trader_id="IC-001"))
    engine.add_venue(
        venue=NSE,
        oms_type=OmsType.NETTING,
        account_type=AccountType.CASH,
        base_currency=INR,
        starting_balances=[Money(1_000_000, INR)],
    )
    for inst in relevant:
        engine.add_instrument(inst)

    bar_spec  = BarSpecification(5, BarAggregation.MINUTE, PriceType.LAST)
    bar_count = 0
    for inst in relevant:
        bars = catalog.bars([BarType(inst.id, bar_spec, AggregationSource.EXTERNAL)])
        if bars:
            engine.add_data(bars)
            bar_count += len(bars)

    print(f"Loaded {bar_count:,} bars for {len(relevant)} instruments")

    engine.add_strategy(IronCondor(config=IronCondorConfig(
        underlying="NIFTY",
        short_strike_offset=short_offset,
        wing_width=wing_width,
        lots=lots,
        stop_loss_multiple=stop_loss,
        entry_weekday=0,
        db_path=snap_path,
    )))
    engine.run()

    print("\n=== ORDER FILLS ===")
    fills = engine.trader.generate_order_fills_report()
    print(fills.to_string() if not fills.empty else "No fills")

    print("\n=== POSITIONS ===")
    positions = engine.trader.generate_positions_report()
    print(positions.to_string() if not positions.empty else "No positions")

    print("\n=== ACCOUNT ===")
    account = engine.trader.generate_account_report(NSE)
    print(account.to_string() if not account.empty else "No account data")

    engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--from",          dest="from_date",     default="2025-09-01")
    parser.add_argument("--to",            dest="to_date",       default="2026-06-03")
    parser.add_argument("--short-offset",  dest="short_offset",  type=int,   default=2)
    parser.add_argument("--wing-width",    dest="wing_width",    type=int,   default=2)
    parser.add_argument("--lots",          dest="lots",          type=int,   default=1)
    parser.add_argument("--stop-loss",     dest="stop_loss",     type=float, default=2.0)
    args = parser.parse_args()
    run_backtest(
        from_date=date.fromisoformat(args.from_date),
        to_date=date.fromisoformat(args.to_date),
        short_offset=args.short_offset,
        wing_width=args.wing_width,
        lots=args.lots,
        stop_loss=args.stop_loss,
    )
