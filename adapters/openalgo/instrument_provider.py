import requests
from nautilus_trader.common.providers import InstrumentProvider
from nautilus_trader.model.instruments import Equity, FuturesContract, OptionContract
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.model.objects import Price, Quantity, Currency
from nautilus_trader.model.enums import AssetClass, OptionKind
from nautilus_trader.config import InstrumentProviderConfig


NSE = Venue("NSE")
NFO = Venue("NFO")
INR = Currency.from_str("INR")


class OpenAlgoInstrumentProvider(InstrumentProvider):

    def __init__(self, client, config: InstrumentProviderConfig):
        super().__init__(config=config)
        self._client = client

    def _fetch_symbols(self, exchange: str) -> list[dict]:
        url = f"{self._client.base_url}/api/v1/symbols"
        headers = {"x-api-key": self._client.api_key}
        try:
            r = requests.get(url, headers=headers, params={"exchange": exchange}, timeout=10)
            r.raise_for_status()
            return r.json() if isinstance(r.json(), list) else []
        except Exception as e:
            self._log.warning(f"Failed to fetch symbols for {exchange}: {e}")
            return []

    async def load_all_async(self, filters=None):
        for exchange in ["NSE", "BSE", "NFO", "MCX"]:
            symbols = self._fetch_symbols(exchange)
            for sym in symbols:
                inst = self._parse_instrument(sym, exchange)
                if inst:
                    self.add(inst)
        self._log.info(f"Loaded {len(self._instruments)} instruments from OpenAlgo")

    def _parse_instrument(self, sym: dict, exchange: str):
        try:
            symbol_str = sym.get("symbol", sym.get("tradingsymbol", ""))
            if not symbol_str:
                return None
            venue = Venue(exchange)
            inst_id = InstrumentId(Symbol(symbol_str), venue)
            inst_type = sym.get("instrument_type", sym.get("instrumenttype", "EQ"))

            if inst_type in ("EQ", "BE"):
                return Equity(
                    instrument_id=inst_id,
                    raw_symbol=Symbol(symbol_str),
                    currency=INR,
                    price_precision=2,
                    price_increment=Price.from_str("0.05"),
                    lot_size=Quantity.from_str("1"),
                    ts_event=0,
                    ts_init=0,
                )
        except Exception:
            pass
        return None
