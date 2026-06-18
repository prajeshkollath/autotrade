"""
EMA Crossover Strategy — Equity
================================
Entry : Fast EMA(10) crosses above Slow EMA(20) -> BUY
Exit  : Fast EMA(10) crosses below Slow EMA(20) -> SELL (close long)

Same file runs as:
  - Backtest : BacktestEngine + yfinance historical bars
  - Live/Paper: TradingNode + OpenAlgo adapter -> Zerodha (or Sandbox)

HOW TO RUN (backtest):
  cd ~/autotrade
  .venv/bin/python strategies/equity/ema_crossover.py

HOW TO RUN (paper trade):
  .venv/bin/python strategies/equity/ema_crossover.py --live
"""
import argparse
import sys
sys.path.insert(0, "/home/freed/autotrade")

from decimal import Decimal
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading.strategy import Strategy
from nautilus_trader.config import StrategyConfig
from nautilus_trader.indicators import ExponentialMovingAverage


class EmaCrossoverConfig(StrategyConfig, frozen=True):
    instrument_id: str
    bar_type: str
    fast_ema: int = 10
    slow_ema: int = 20
    trade_size: Decimal = Decimal("1")


class EmaCrossover(Strategy):

    def __init__(self, config: EmaCrossoverConfig):
        super().__init__(config)
        self.fast_ema = ExponentialMovingAverage(config.fast_ema)
        self.slow_ema = ExponentialMovingAverage(config.slow_ema)
        self._instrument_id = InstrumentId.from_str(config.instrument_id)
        self._bar_type = BarType.from_str(config.bar_type)
        self._in_position = False

    def on_start(self):
        self.subscribe_bars(self._bar_type)
        self.log.info(f"EmaCrossover started on {self._instrument_id}")

    def on_bar(self, bar: Bar):
        self.fast_ema.update_raw(bar.close.as_double())
        self.slow_ema.update_raw(bar.close.as_double())

        if not self.fast_ema.initialized or not self.slow_ema.initialized:
            return

        fast = self.fast_ema.value
        slow = self.slow_ema.value

        if fast > slow and not self._in_position:
            self._enter_long()
        elif fast < slow and self._in_position:
            self._exit_long()

    def _enter_long(self):
        order = self.order_factory.market(
            instrument_id=self._instrument_id,
            order_side=OrderSide.BUY,
            quantity=self.cache.instrument(self._instrument_id).make_qty(self.config.trade_size),
        )
        self.submit_order(order)
        self._in_position = True
        self.log.info(f"LONG entry -- fast={self.fast_ema.value:.2f} slow={self.slow_ema.value:.2f}")

    def _exit_long(self):
        self.close_all_positions(self._instrument_id)
        self._in_position = False
        self.log.info(f"LONG exit -- fast={self.fast_ema.value:.2f} slow={self.slow_ema.value:.2f}")

    def on_stop(self):
        self.cancel_all_orders(self._instrument_id)
        self.close_all_positions(self._instrument_id)


def run_backtest():
    import pandas as pd
    import yfinance as yf
    from nautilus_trader.backtest.engine import BacktestEngine
    from nautilus_trader.config import BacktestEngineConfig
    from nautilus_trader.model.currencies import INR
    from nautilus_trader.model.enums import AccountType, OmsType, BarAggregation, PriceType, AggregationSource
    from nautilus_trader.model.identifiers import Venue, Symbol
    from nautilus_trader.model.objects import Money, Price, Quantity
    from nautilus_trader.model.instruments import Equity
    from nautilus_trader.model.data import BarSpecification

    print("Fetching RELIANCE daily data from yfinance (2023-2024)...")
    raw = yf.download("RELIANCE.NS", start="2023-01-01", end="2024-12-31", interval="1d", progress=False)
    if raw.empty:
        print("No data. Check internet.")
        return
    print(f"Fetched {len(raw)} bars")

    engine = BacktestEngine(config=BacktestEngineConfig(trader_id="BACKTESTER-001"))
    NSE = Venue("NSE")

    instrument = Equity(
        instrument_id=InstrumentId(Symbol("RELIANCE"), NSE),
        raw_symbol=Symbol("RELIANCE"),
        currency=INR,
        price_precision=2,
        price_increment=Price.from_str("0.05"),
        lot_size=Quantity.from_str("1"),
        ts_event=0,
        ts_init=0,
    )
    engine.add_venue(
        venue=NSE,
        oms_type=OmsType.NETTING,
        account_type=AccountType.CASH,
        base_currency=INR,
        starting_balances=[Money(1_000_000, INR)],
    )
    engine.add_instrument(instrument)

    bar_type = BarType(
        instrument_id=instrument.id,
        bar_spec=BarSpecification(1, BarAggregation.DAY, PriceType.LAST),
        aggregation_source=AggregationSource.EXTERNAL,
    )

    bars = []
    for ts, row in raw.iterrows():
        try:
            def val(x):
                return float(x.iloc[0]) if hasattr(x, "iloc") else float(x)
            ts_ns = int(pd.Timestamp(ts, tz="UTC").timestamp() * 1e9)
            bar = Bar(
                bar_type=bar_type,
                open=Price(val(row["Open"]), precision=2),
                high=Price(val(row["High"]), precision=2),
                low=Price(val(row["Low"]), precision=2),
                close=Price(val(row["Close"]), precision=2),
                volume=Quantity(val(row["Volume"]), precision=0),
                ts_event=ts_ns,
                ts_init=ts_ns,
            )
            bars.append(bar)
        except Exception:
            continue

    engine.add_data(bars)

    strategy = EmaCrossover(config=EmaCrossoverConfig(
        instrument_id="RELIANCE.NSE",
        bar_type="RELIANCE.NSE-1-DAY-LAST-EXTERNAL",
        fast_ema=10,
        slow_ema=20,
        trade_size=Decimal("1"),
    ))
    engine.add_strategy(strategy)
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
    parser.add_argument("--live", action="store_true", help="Run via OpenAlgo Sandbox")
    args = parser.parse_args()
    if args.live:
        print("Live/paper mode: OpenAlgo TradingNode integration (wired in Stage 5)")
    else:
        run_backtest()
