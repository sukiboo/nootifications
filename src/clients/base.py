import asyncio
import logging
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Generic, Protocol, TypeVar

from src.schemas import PriceUpdate

logger = logging.getLogger(__name__)


class _ReconnectConfig(Protocol):
    max_reconnect_attempts: int
    reconnect_delay: int
    stale_timeout_seconds: int


TConfig = TypeVar("TConfig", bound=_ReconnectConfig)


class BasePriceClient(ABC, Generic[TConfig]):
    """
    Abstract base class for price monitoring clients.

    Different asset types (crypto, stocks) may have very different
    connection mechanisms, authentication, and data formats.
    This base class defines the minimal interface needed by the bot.

    Subclasses must initialize ``_config``, ``_price_queue``,
    ``_subscribed_tickers`` and ``_connected`` in their ``__init__``.
    """

    _config: TConfig
    _price_queue: asyncio.Queue[PriceUpdate]
    _subscribed_tickers: list[str]
    _connected: bool

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable name for this client (for logging)."""
        ...

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """Whether the client is currently connected."""
        ...

    @abstractmethod
    async def connect(self) -> None:
        """
        Establish connection to the data source.

        Should handle authentication if required.
        May raise exceptions on connection failure.
        """
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """
        Gracefully close the connection.

        Should be idempotent (safe to call multiple times).
        """
        ...

    @abstractmethod
    async def subscribe(self, tickers: list[str]) -> None:
        """
        Subscribe to price updates for the given tickers.

        Args:
            tickers: List of ticker symbols in the format expected by this client
        """
        ...

    @abstractmethod
    def validate_tickers(self, tickers: list[str]) -> None:
        """
        Validate that all tickers are supported by this client.

        Args:
            tickers: List of ticker symbols to validate

        Raises:
            ValueError: If any ticker is invalid or unsupported
        """
        ...

    async def price_updates(self) -> AsyncIterator[PriceUpdate]:
        """
        Async iterator yielding price updates.

        Pulls from ``self._price_queue`` (populated by the subclass's receive
        path) and handles reconnection on connection loss.

        Usage:
            async for update in client.price_updates():
                print(f"{update.ticker}: {update.price}")
        """
        reconnect_attempts = 0
        last_update_time = time.monotonic()

        while True:
            try:
                try:
                    update = await asyncio.wait_for(self._price_queue.get(), timeout=30.0)
                    reconnect_attempts = 0
                    last_update_time = time.monotonic()
                    yield update
                except asyncio.TimeoutError:
                    if not self._connected:
                        raise RuntimeError(f"{self.name} WebSocket disconnected")
                    idle = time.monotonic() - last_update_time
                    if idle > self._config.stale_timeout_seconds:
                        raise RuntimeError(
                            f"{self.name} WebSocket stale -- no updates in {int(idle)}s"
                        )
                    continue

            except asyncio.CancelledError:
                logger.info("%s price update stream cancelled", self.name)
                raise

            except Exception as e:
                logger.error("Error in %s price update stream: %s", self.name, e)
                self._connected = False

                if reconnect_attempts >= self._config.max_reconnect_attempts:
                    logger.error("Max reconnection attempts reached, giving up")
                    raise

                reconnect_attempts += 1
                logger.info(
                    "Attempting reconnection %d/%d in %ds...",
                    reconnect_attempts,
                    self._config.max_reconnect_attempts,
                    self._config.reconnect_delay,
                )
                await asyncio.sleep(self._config.reconnect_delay)

                try:
                    await self.disconnect()
                    await self.connect()
                    if self._subscribed_tickers:
                        await self.subscribe(self._subscribed_tickers)
                    last_update_time = time.monotonic()
                except Exception as reconnect_error:
                    logger.error("Reconnection failed: %s", reconnect_error)
