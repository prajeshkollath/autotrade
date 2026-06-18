import asyncio
import json
import requests
import websockets
from datetime import datetime, timezone

from nautilus_trader.live.data_client import LiveDataClient
from nautilus_trader.model.data import QuoteTick, TradeTick, Bar, BarType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Price, Quantity


class OpenAlgoDataClient(LiveDataClient):

    def __init__(self, loop, client, msgbus, cache, clock, config):
        super().__init__(
            loop=loop,
            client_id=client.client_id,
            venue=client.venue,
            msgbus=msgbus,
            cache=cache,
            clock=clock,
            config=config,
        )
        self._client = client
        self._ws = None
        self._subscribed_quotes = set()
        self._subscribed_bars = set()

    async def _connect(self):
        self._log.info(f"Connecting to OpenAlgo WebSocket: {self._client.ws_url}")
        try:
            self._ws = await websockets.connect(self._client.ws_url)
            # Authenticate
            await self._ws.send(json.dumps({
                "action": "authenticate",
                "api_key": self._client.api_key
            }))
            asyncio.create_task(self._listen())
            self._log.info("OpenAlgo WebSocket connected")
        except Exception as e:
            self._log.error(f"WebSocket connect failed: {e}")

    async def _disconnect(self):
        if self._ws:
            await self._ws.close()
            self._ws = None

    async def _listen(self):
        try:
            async for msg in self._ws:
                await self._handle_message(json.loads(msg))
        except Exception as e:
            self._log.warning(f"WebSocket disconnected: {e}")

    async def _handle_message(self, data: dict):
        if data.get("type") not in ("ltp", "quote"):
            return
        symbol = data.get("symbol", "")
        exchange = data.get("exchange", "NSE")
        try:
            from nautilus_trader.model.identifiers import Symbol, Venue
            inst_id = InstrumentId(Symbol(symbol), Venue(exchange))
            ltp = float(data.get("ltp", 0))
            ts = int(datetime.now(timezone.utc).timestamp() * 1e9)
            quote = QuoteTick(
                instrument_id=inst_id,
                bid_price=Price(ltp, precision=2),
                ask_price=Price(ltp, precision=2),
                bid_size=Quantity(0, precision=0),
                ask_size=Quantity(0, precision=0),
                ts_event=ts,
                ts_init=ts,
            )
            self._handle_data(quote)
        except Exception as e:
            self._log.warning(f"Error handling tick for {symbol}: {e}")

    def subscribe_quote_ticks(self, instrument_id: InstrumentId, params=None):
        self._subscribed_quotes.add(instrument_id)
        if self._ws:
            asyncio.create_task(self._ws.send(json.dumps({
                "action": "subscribe",
                "symbol": instrument_id.symbol.value,
                "exchange": instrument_id.venue.value,
                "mode": "ltp"
            })))

    def unsubscribe_quote_ticks(self, instrument_id: InstrumentId, params=None):
        self._subscribed_quotes.discard(instrument_id)

    def subscribe_bars(self, bar_type: BarType, params=None):
        self._subscribed_bars.add(bar_type)

    def unsubscribe_bars(self, bar_type: BarType, params=None):
        self._subscribed_bars.discard(bar_type)

    def request_bars(self, bar_type, start=None, end=None, limit=None, correlation_id=None, params=None):
        pass

    def request_quote_ticks(self, instrument_id, start=None, end=None, limit=None, correlation_id=None, params=None):
        pass

    def request_trade_ticks(self, instrument_id, start=None, end=None, limit=None, correlation_id=None, params=None):
        pass
