from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from src.schemas import PriceUpdate


class BasePriceClient(ABC):
    """
    Abstract base class for price monitoring clients.

    Different asset types (crypto, stocks) may have very different
    connection mechanisms, authentication, and data formats.
    This base class defines the minimal interface needed by the bot.
    """

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
    def price_updates(self) -> AsyncIterator[PriceUpdate]:
        """
        Async iterator yielding price updates.

        Usage:
            async for update in client.price_updates():
                print(f"{update.ticker}: {update.price}")

        Should handle reconnection internally where appropriate.
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
