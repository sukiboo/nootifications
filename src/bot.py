import asyncio
import logging

from src.clients.alpaca import AlpacaClient
from src.clients.base import BasePriceClient
from src.clients.kraken import KrakenClient
from src.schemas import AlertInfo, Client, MonitorConfig, PriceUpdate
from src.telegram import TelegramNotifier
from src.utils import PriceStateManager, Settings

logger = logging.getLogger(__name__)


class NootificationsBot:
    """
    Main bot that orchestrates price monitoring and notifications.

    Manages multiple price monitoring clients and dispatches
    Telegram alerts when price thresholds are crossed.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.notifier = TelegramNotifier(
            bot_token=settings.env.telegram_bot_token,
            user_id=settings.env.telegram_user_id,
        )
        self.price_state = PriceStateManager()
        self._clients: list[BasePriceClient] = []
        self._running = False
        self._smoothed_prices: dict[str, float] = {}

        # Build ticker -> monitor config mapping
        self._monitors: dict[str, MonitorConfig] = {m.ticker: m for m in settings.app.assets}

    async def run(self) -> None:
        """Main entry point -- start monitoring and run until cancelled."""
        self._running = True
        logger.info("Starting %s", self.settings.bot_name)

        # Initialize clients based on configured assets
        kraken_tickers = [m.ticker for m in self.settings.app.assets if m.client == Client.KRAKEN]
        alpaca_tickers = [m.ticker for m in self.settings.app.assets if m.client == Client.ALPACA]

        if kraken_tickers:
            kraken_client = KrakenClient(self.settings.app.clients.kraken)
            self._clients.append(kraken_client)
        if alpaca_tickers:
            key = self.settings.env.alpaca_api_key
            secret = self.settings.env.alpaca_api_secret
            if not key or not secret:
                raise RuntimeError(
                    "Alpaca assets configured but ALPACA_API_KEY / ALPACA_API_SECRET not set. "
                    "Add them to .env (from your Alpaca account)."
                )
            alpaca_client = AlpacaClient(
                self.settings.app.clients.alpaca,
                api_key=key,
                api_secret=secret,
            )
            self._clients.append(alpaca_client)

        if not self._clients:
            logger.error("No valid monitors configured, nothing to do")
            return

        try:
            # Send startup notification
            await self.notifier.send(
                f"🔆 {self.settings.bot_name}: monitoring {len(self._monitors)} assets"
            )

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
            await self.notifier.send(f"❌ Bot crashed: {e}")
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
        # Find the monitor config for this ticker
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

        reference = self.price_state.get_price(update.ticker)
        if reference is None:
            logger.info("Initial price for %s: $%.2f", monitor.name, price)
            self.price_state.set_price(update.ticker, price)
            return

        if alert := self._check_threshold(monitor, reference, price):
            await self._send_alert(alert)
            self.price_state.set_price(update.ticker, price)

    def _get_smoothing(self, source: str) -> float:
        if source == "kraken":
            return self.settings.app.clients.kraken.smoothing
        if source == "alpaca":
            return self.settings.app.clients.alpaca.smoothing
        return 0.0

    def _check_threshold(
        self, monitor: MonitorConfig, old_price: float, new_price: float
    ) -> AlertInfo | None:
        change_pct = (new_price - old_price) / (old_price + 1e-9)

        if monitor.is_percentage:
            threshold_crossed = abs(change_pct) > monitor.delta
        else:
            old_interval = int(old_price // monitor.delta)
            new_interval = int(new_price // monitor.delta)
            threshold_crossed = old_interval != new_interval
            new_price = new_interval * monitor.delta

        if not threshold_crossed:
            return None

        return AlertInfo(
            monitor=monitor,
            old_price=old_price,
            new_price=new_price,
            change_pct=change_pct,
        )

    async def _send_alert(self, alert: AlertInfo) -> None:
        direction = "up" if alert.change_pct > 0 else "down"
        emoji = "📈" if alert.change_pct > 0 else "📉"
        message = (
            f"{emoji} {alert.monitor.name} is {direction} "
            f"{abs(alert.change_pct):.2%} to ${alert.new_price:,.2f}"
        )
        logger.info(
            "Alert: %s %s %.2f%% (%.2f -> %.2f)",
            alert.monitor.name,
            direction,
            abs(alert.change_pct) * 100,
            alert.old_price,
            alert.new_price,
        )
        await self.notifier.send(message, silent=True)

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
