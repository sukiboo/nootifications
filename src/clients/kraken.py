import asyncio
import logging
import time
from typing import Any

from kraken.spot import Market, SpotWSClient

from src.clients.base import BasePriceClient
from src.schemas import KrakenConfig, PriceUpdate

logger = logging.getLogger(__name__)


class KrakenClient(BasePriceClient[KrakenConfig]):
    """
    Kraken WebSocket client using the official python-kraken-sdk.

    Uses WebSocket API v2 for real-time ticker data.
    """

    def __init__(self, config: KrakenConfig | None = None) -> None:
        self._config = config or KrakenConfig()
        self._client: _KrakenWSHandler | None = None
        self._connected = False
        self._subscribed_tickers: list[str] = []
        self._price_queue: asyncio.Queue[PriceUpdate] = asyncio.Queue()

    @property
    def name(self) -> str:
        return "Kraken"

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def connect(self) -> None:
        """Establish WebSocket connection to Kraken."""
        if self._connected:
            return

        logger.info("Connecting to Kraken WebSocket...")
        self._client = _KrakenWSHandler(self._price_queue, self._config.throttle_seconds)
        await self._client.start()
        self._connected = True
        logger.info("Connected to Kraken WebSocket")

    async def disconnect(self) -> None:
        """Close the WebSocket connection."""
        if self._client:
            try:
                await self._client.close()
            except Exception as e:
                logger.warning("Error closing Kraken connection: %s", e)
            finally:
                self._client = None
                self._connected = False
                logger.info("Disconnected from Kraken WebSocket")

    async def subscribe(self, tickers: list[str]) -> None:
        """
        Subscribe to ticker updates.

        Args:
            tickers: Kraken ticker symbols in WS v2 format (e.g., ["BTC/USD", "ETH/USD"])
        """
        if not self._client:
            raise RuntimeError("Client not connected. Call connect() first.")

        logger.info("Subscribing to tickers: %s", tickers)
        await self._client.subscribe(params={"channel": "ticker", "symbol": tickers})
        self._subscribed_tickers = tickers
        logger.info("Subscribed to %d tickers", len(tickers))

    def validate_tickers(self, tickers: list[str]) -> None:
        """Validate tickers against Kraken's available trading pairs."""
        if not tickers:
            return

        # Check for deprecated XBT format first
        deprecated = [t for t in tickers if "XBT" in t]
        if deprecated:
            raise ValueError(
                f"Deprecated ticker format: {deprecated}. "
                "Use 'BTC' instead of 'XBT' (e.g. 'BTC/USD')."
            )

        market = Market()
        pairs_response = market.get_asset_pairs()

        # Build set of valid symbols in WS v2 format (e.g., "BTC/USD")
        # REST API returns wsname in v1 format (XBT), convert to v2 format (BTC)
        valid_symbols: set[str] = set()
        for pair_info in pairs_response.values():
            if isinstance(pair_info, dict) and "wsname" in pair_info:
                wsname = pair_info["wsname"]
                # Convert v1 format (XBT) to v2 format (BTC)
                valid_symbols.add(wsname.replace("XBT", "BTC"))

        invalid = [t for t in tickers if t not in valid_symbols]
        if invalid:
            raise ValueError(
                f"Invalid Kraken ticker(s): {invalid}. " "Use format like 'BTC/USD', 'ETH/USD'."
            )


class _KrakenWSHandler(SpotWSClient):
    """
    Internal WebSocket handler that extends SpotWSClient.

    Processes incoming messages and puts price updates on the queue.
    """

    def __init__(self, price_queue: asyncio.Queue[PriceUpdate], throttle_seconds: float) -> None:
        super().__init__()
        self._price_queue = price_queue
        self._throttle_seconds = throttle_seconds
        self._last_update: dict[str, float] = {}

    async def on_message(self, message: dict[str, Any] | list[Any]) -> None:
        """Process incoming WebSocket messages."""
        if not isinstance(message, dict):
            return
        # Skip heartbeats and system messages
        if message.get("channel") == "heartbeat":
            return
        if message.get("method") in ("pong", "subscribe", "unsubscribe"):
            return

        # Process ticker updates
        if message.get("channel") == "ticker":
            await self._process_ticker(message)

    async def _process_ticker(self, message: dict[str, Any]) -> None:
        """Extract price from ticker message and queue update."""
        try:
            data = message.get("data", [])
            if not data:
                return

            for ticker_data in data:
                symbol = ticker_data.get("symbol")
                # last price is in the 'last' field
                last_price = ticker_data.get("last")

                if symbol and last_price is not None:
                    now = time.monotonic()
                    if now - self._last_update.get(symbol, 0) < self._throttle_seconds:
                        continue
                    self._last_update[symbol] = now
                    price = float(last_price)
                    update = PriceUpdate(ticker=symbol, price=price, source="kraken")
                    await self._price_queue.put(update)
                    logger.debug("Price update: %s = %.2f", symbol, price)

        except (KeyError, TypeError, ValueError) as e:
            logger.warning("Failed to parse ticker message: %s - %s", message, e)
