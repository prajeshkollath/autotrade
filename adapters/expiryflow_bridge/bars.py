from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from nautilus_trader.model.data import Bar, BarType, BarSpecification
from nautilus_trader.model.enums import BarAggregation, PriceType, AggregationSource
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Price, Quantity

IST = ZoneInfo("Asia/Kolkata")

INTERVAL_MAP = {
    "1": (1, BarAggregation.MINUTE),
    "5": (5, BarAggregation.MINUTE),
    "15": (15, BarAggregation.MINUTE),
    "25": (25, BarAggregation.MINUTE),
    "60": (1, BarAggregation.HOUR),
    "D": (1, BarAggregation.DAY),
}


def make_bar_type(instrument_id: InstrumentId, interval: str) -> BarType:
    step, aggregation = INTERVAL_MAP.get(interval, (5, BarAggregation.MINUTE))
    return BarType(
        instrument_id=instrument_id,
        bar_spec=BarSpecification(step=step, aggregation=aggregation, price_type=PriceType.LAST),
        aggregation_source=AggregationSource.EXTERNAL,
    )


def make_bar(bar_type: BarType, row: dict) -> Bar | None:
    try:
        ts_raw = row["timestamp"]
        if isinstance(ts_raw, datetime):
            dt = ts_raw if ts_raw.tzinfo else ts_raw.replace(tzinfo=IST)
        elif isinstance(ts_raw, str):
            dt = datetime.fromisoformat(ts_raw)
            if not dt.tzinfo:
                dt = dt.replace(tzinfo=IST)
        else:
            dt = datetime.fromtimestamp(float(ts_raw), tz=IST)

        ts_ns = int(dt.astimezone(timezone.utc).timestamp() * 1e9)

        return Bar(
            bar_type=bar_type,
            open=Price(float(row["open"]), precision=2),
            high=Price(float(row["high"]), precision=2),
            low=Price(float(row["low"]), precision=2),
            close=Price(float(row["close"]), precision=2),
            volume=Quantity(float(row.get("volume", 0) or 0), precision=0),
            ts_event=ts_ns,
            ts_init=ts_ns,
        )
    except Exception:
        return None
