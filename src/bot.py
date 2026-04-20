import asyncio
import logging

from src.clients.alpaca import AlpacaClient
from src.clients.base import BasePriceClient
from src.clients.kraken import KrakenClient
from src.notifications import AlertHandler
from src.schemas import Client, MonitorConfig, PriceUpdate
from src.utils import PriceStateManager, Settings

logger = logging.getLogger(__name__)


class NootificationsBot:
    """
    Main bot that orchestrates price monitoring and notifications.

    Manages multiple price monitoring clients and dispatches
    Telegram alerts when price thresholds are crossed.
    """

    def __init__(self, settings: Settings, config_path: str = "settings.yaml") -> None:
        self._running = False
        self.settings = settings
        self.price_state = PriceStateManager()
        self._clients: list[BasePriceClient] = []
        self._smoothed_prices: dict[str, float] = {}

        # Build ticker -> monitor config mapping
        self._monitors: dict[str, MonitorConfig] = {m.ticker: m for m in settings.app.assets}

        # Set up alerts
        self.alerts = AlertHandler.create(
            bot_token=settings.env.telegram_bot_token,
            user_id=settings.env.telegram_user_id,
            config_path=config_path,
        )

    async def run(self) -> None:
        """Main entry point -- start monitoring and run until cancelled."""
        self._running = True
        logger.info("Starting %s", self.settings.bot_name)

        # Initialize clients based on configured assets
        kraken_tickers = [m.ticker for m in self.settings.app.assets if m.client == Client.KRAKEN]
        alpaca_tickers = [m.ticker for m in self.settings.app.assets if m.client == Client.ALPACA]

        try:
            # Initialize and validate clients
            if kraken_tickers:
                kraken_client = KrakenClient(self.settings.app.clients.kraken)
                kraken_client.validate_tickers(kraken_tickers)
                self._clients.append(kraken_client)
            if alpaca_tickers:
                key = self.settings.env.alpaca_api_key
                secret = self.settings.env.alpaca_api_secret
                if not key or not secret:
                    raise RuntimeError(
                        "Alpaca assets configured but ALPACA_API_KEY / ALPACA_API_SECRET not set. "
                        "Add them to `.env` (from your Alpaca account)."
                    )
                alpaca_client = AlpacaClient(
                    self.settings.app.clients.alpaca,
                    api_key=key,
                    api_secret=secret,
                )
                alpaca_client.validate_tickers(alpaca_tickers)
                self._clients.append(alpaca_client)

            if not self._clients:
                logger.error("No valid monitors configured, nothing to do")
                return

            # Send startup notification
            await self.alerts.notify_startup(self.settings.bot_name, len(self._monitors))

            # Start all clients
            tasks = []
            for client in self._clients:
                await client.connect()

                # Subscribe to relevant tickers
                if isinstance(client, KrakenClient):
                    await client.subscribe(kraken_tickers)
                    tasks.append(asyncio.create_task(self._monitor_client(client)))
                elif isinstance(client, AlpacaClient):
                    await client.subscribe(alpaca_tickers)
                    tasks.append(asyncio.create_task(self._monitor_client(client)))

            # Wait for all monitoring tasks
            await asyncio.gather(*tasks)

        except asyncio.CancelledError:
            logger.info("Bot shutdown requested")
        except Exception as e:
            logger.exception("Fatal error in bot: %s", e)
            await self.alerts.notify_error(e)
            raise
        finally:
            await self._shutdown()

    async def _monitor_client(self, client: BasePriceClient) -> None:
        """Monitor a single price client for updates."""
        logger.info("Starting price monitor for %s", client.name)

        async for update in client.price_updates():
            if not self._running:
                break
            await self._process_price_update(update)

    async def _process_price_update(self, update: PriceUpdate) -> None:
        """Process a price update and check for alert conditions."""
        monitor = self._monitors.get(update.ticker)
        if not monitor:
            raise ValueError(
                f"Received price update for unknown ticker: {update.ticker}. "
                f"Configured tickers: {list(self._monitors.keys())}"
            )

        # Apply EMA smoothing: price = s * prev + (1-s) * raw
        s = self._get_smoothing(update.source)
        prev = self._smoothed_prices.get(update.ticker, update.price)
        price = s * prev + (1 - s) * update.price
        self._smoothed_prices[update.ticker] = price

        # Get reference price (or initialize if first update)
        reference = self.price_state.get_price(update.ticker)
        if reference is None:
            logger.info("Initial price for %s: $%.2f", monitor.name, price)
            await self.price_state.set_price(update.ticker, price)
            return

        # Check alerts and update reference if any triggered
        if await self.alerts.check_and_notify(monitor, reference, price):
            await self.price_state.set_price(update.ticker, price)

    def _get_smoothing(self, source: str) -> float:
        if source == "kraken":
            return self.settings.app.clients.kraken.smoothing
        elif source == "alpaca":
            return self.settings.app.clients.alpaca.smoothing
        else:
            return 0.0

    async def _shutdown(self) -> None:
        """Gracefully shutdown all clients."""
        self._running = False
        logger.info("Shutting down...")

        for client in self._clients:
            try:
                await client.disconnect()
            except Exception as e:
                logger.warning("Error disconnecting %s: %s", client.name, e)

        logger.info("Shutdown complete")
