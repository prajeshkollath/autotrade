from datetime import date, timezone, datetime
from nautilus_trader.model.instruments import OptionContract
from nautilus_trader.model.enums import OptionKind, AssetClass
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.model.objects import Price, Quantity, Currency

# NSE lot sizes as of 2025-2026
LOT_SIZES = {
    "NIFTY":      65,
    "BANKNIFTY":  30,
    "SENSEX":     20,
    "FINNIFTY":   40,
    "MIDCPNIFTY": 75,
}

VENUE = Venue("NSE")
INR = Currency.from_str("INR")


def make_instrument_id(underlying: str, expiry: date, strike: float, option_type: str) -> InstrumentId:
    expiry_str = expiry.strftime("%Y%m%d")
    strike_str = str(int(strike))
    symbol_str = f"{underlying}_{expiry_str}_{strike_str}_{option_type}"
    return InstrumentId(Symbol(symbol_str), VENUE)


def make_option_contract(
    underlying: str,
    expiry: date,
    strike_price: float,
    option_type: str,
    ts_date: date,
) -> OptionContract:
    instrument_id = make_instrument_id(underlying, expiry, strike_price, option_type)
    kind       = OptionKind.CALL if option_type == "CE" else OptionKind.PUT
    lot_size   = LOT_SIZES.get(underlying, 65)

    activation_dt = datetime(ts_date.year, ts_date.month, ts_date.day, tzinfo=timezone.utc)
    expiration_dt = datetime(expiry.year, expiry.month, expiry.day, 10, 0, tzinfo=timezone.utc)

    return OptionContract(
        instrument_id=instrument_id,
        raw_symbol=instrument_id.symbol,
        asset_class=AssetClass.INDEX,
        currency=INR,
        price_precision=2,
        price_increment=Price.from_str("0.05"),
        multiplier=Quantity.from_str("1"),
        lot_size=Quantity.from_str(str(lot_size)),
        underlying=underlying,
        option_kind=kind,
        activation_ns=int(activation_dt.timestamp() * 1e9),
        expiration_ns=int(expiration_dt.timestamp() * 1e9),
        strike_price=Price(strike_price, precision=2),
        ts_event=int(activation_dt.timestamp() * 1e9),
        ts_init=int(activation_dt.timestamp() * 1e9),
    )
