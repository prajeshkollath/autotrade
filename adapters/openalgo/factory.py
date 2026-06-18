from nautilus_trader.live.factories import LiveDataClientFactory, LiveExecClientFactory
from nautilus_trader.model.identifiers import ClientId, Venue

from .config import OpenAlgoDataClientConfig, OpenAlgoExecClientConfig
from .data_client import OpenAlgoDataClient
from .execution_client import OpenAlgoExecClient
from .instrument_provider import OpenAlgoInstrumentProvider


class _OpenAlgoClient:
    """Lightweight holder for shared config passed to data + exec clients."""
    def __init__(self, config, venue):
        self.api_key = config.api_key
        self.base_url = config.base_url
        self.ws_url = getattr(config, 'ws_url', 'ws://localhost:8765')
        self.client_id = ClientId("OPENALGO")
        self.venue = Venue(venue)


class OpenAlgoLiveDataClientFactory(LiveDataClientFactory):

    @staticmethod
    def create(loop, name, config: OpenAlgoDataClientConfig, msgbus, cache, clock):
        venue = getattr(config, 'venue', 'NSE')
        client = _OpenAlgoClient(config, venue)
        provider = OpenAlgoInstrumentProvider(client=client, config=config)
        return OpenAlgoDataClient(
            loop=loop,
            client=client,
            msgbus=msgbus,
            cache=cache,
            clock=clock,
            config=config,
        )


class OpenAlgoLiveExecClientFactory(LiveExecClientFactory):

    @staticmethod
    def create(loop, name, config: OpenAlgoExecClientConfig, msgbus, cache, clock):
        venue = getattr(config, 'venue', 'NSE')
        client = _OpenAlgoClient(config, venue)
        return OpenAlgoExecClient(
            loop=loop,
            client=client,
            msgbus=msgbus,
            cache=cache,
            clock=clock,
            config=config,
        )
