from nautilus_trader.config import LiveDataClientConfig, LiveExecClientConfig, InstrumentProviderConfig


class OpenAlgoInstrumentProviderConfig(InstrumentProviderConfig):
    api_key: str = ""
    base_url: str = "http://localhost:5000"


class OpenAlgoDataClientConfig(LiveDataClientConfig):
    api_key: str = ""
    base_url: str = "http://localhost:5000"
    ws_url: str = "ws://localhost:8765"
    venue: str = "NSE"


class OpenAlgoExecClientConfig(LiveExecClientConfig):
    api_key: str = ""
    base_url: str = "http://localhost:5000"
    account_id: str = "ZERODHA-001"
    venue: str = "NSE"
