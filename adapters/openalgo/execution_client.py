import asyncio
import requests
from datetime import datetime, timezone

from nautilus_trader.live.execution_client import LiveExecutionClient
from nautilus_trader.model.enums import OrderSide, OrderType, LiquiditySide
from nautilus_trader.model.events import OrderAccepted, OrderRejected, OrderFilled
from nautilus_trader.model.identifiers import ClientOrderId, VenueOrderId, AccountId, TradeId
from nautilus_trader.model.objects import Money, Price, Quantity, Currency


PRODUCT_MAP = {
    "CNC": "CNC",
    "MIS": "MIS",
    "NRML": "NRML",
}

ORDER_TYPE_MAP = {
    OrderType.MARKET: "MARKET",
    OrderType.LIMIT: "LIMIT",
    OrderType.STOP_MARKET: "SL-M",
    OrderType.STOP_LIMIT: "SL",
}


class OpenAlgoExecClient(LiveExecutionClient):

    def __init__(self, loop, client, msgbus, cache, clock, config):
        super().__init__(
            loop=loop,
            client_id=client.client_id,
            venue=client.venue,
            oms_type=None,
            account_type=None,
            base_currency=Currency.from_str("INR"),
            msgbus=msgbus,
            cache=cache,
            clock=clock,
            config=config,
        )
        self._client = client
        self._order_map = {}   # client_order_id ? venue_order_id

    def _headers(self):
        return {"x-api-key": self._client.api_key, "Content-Type": "application/json"}

    def _post(self, endpoint: str, payload: dict) -> dict:
        url = f"{self._client.base_url}{endpoint}"
        r = requests.post(url, json=payload, headers=self._headers(), timeout=10)
        r.raise_for_status()
        return r.json()

    def _get(self, endpoint: str, params: dict = None) -> dict:
        url = f"{self._client.base_url}{endpoint}"
        r = requests.get(url, params=params, headers=self._headers(), timeout=10)
        r.raise_for_status()
        return r.json()

    async def _connect(self):
        self._log.info("OpenAlgo ExecClient connected")
        await self._update_account_state()

    async def _disconnect(self):
        self._log.info("OpenAlgo ExecClient disconnected")

    async def _update_account_state(self):
        try:
            funds = self._get("/api/v1/funds")
            self._log.info(f"Funds: {funds}")
        except Exception as e:
            self._log.warning(f"Could not fetch funds: {e}")

    def submit_order(self, command):
        order = command.order
        side = "BUY" if order.side == OrderSide.BUY else "SELL"
        order_type = ORDER_TYPE_MAP.get(order.order_type, "MARKET")
        exchange = order.instrument_id.venue.value

        payload = {
            "apikey": self._client.api_key,
            "strategy": "NautilusTrader",
            "symbol": order.instrument_id.symbol.value,
            "action": side,
            "exchange": exchange,
            "pricetype": order_type,
            "product": "MIS",
            "quantity": str(int(order.quantity)),
        }
        if order.order_type == OrderType.LIMIT:
            payload["price"] = str(float(order.price))

        try:
            result = self._post("/api/v1/placeorder", payload)
            venue_order_id = str(result.get("orderid", ""))
            self._order_map[order.client_order_id] = venue_order_id

            ts = int(datetime.now(timezone.utc).timestamp() * 1e9)
            self._generate_order_accepted(
                strategy_id=command.strategy_id,
                instrument_id=order.instrument_id,
                client_order_id=order.client_order_id,
                venue_order_id=VenueOrderId(venue_order_id),
                ts_event=ts,
            )
            self._log.info(f"Order placed: {venue_order_id} for {order.instrument_id}")
        except Exception as e:
            self._log.error(f"Order submission failed: {e}")
            ts = int(datetime.now(timezone.utc).timestamp() * 1e9)
            self._generate_order_rejected(
                strategy_id=command.strategy_id,
                instrument_id=order.instrument_id,
                client_order_id=order.client_order_id,
                reason=str(e),
                ts_event=ts,
            )

    def cancel_order(self, command):
        venue_order_id = self._order_map.get(command.client_order_id, "")
        if not venue_order_id:
            return
        try:
            self._post("/api/v1/cancelorder", {
                "apikey": self._client.api_key,
                "strategy": "NautilusTrader",
                "orderid": venue_order_id,
            })
        except Exception as e:
            self._log.error(f"Cancel failed: {e}")

    def modify_order(self, command):
        pass

    def query_order(self, command):
        pass
