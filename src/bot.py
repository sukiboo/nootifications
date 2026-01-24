import asyncio
import logging

from src.clients import KrakenClient
from src.clients.base import BasePriceClient
from src.schemas import AlertInfo, AssetType, MonitorConfig, PriceUpdate
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

        # Build ticker -> monitor config mapping
        self._monitors: dict[str, MonitorConfig] = {m.ticker: m for m in settings.app.monitoring}

    async def run(self) -> None:
        """Main entry point - start monitoring and run until cancelled."""
        self._running = True
        logger.info("Starting %s", self.settings.bot_name)

        # Initialize clients based on configured monitors
        crypto_tickers = [
            m.ticker for m in self.settings.app.monitoring if m.type == AssetType.CRYPTO
        ]

        if crypto_tickers:
            kraken_client = KrakenClient()
            self._clients.append(kraken_client)

        # TODO: Add stock client when implemented
        stock_tickers = [
            m.ticker for m in self.settings.app.monitoring if m.type == AssetType.STOCK
        ]
        if stock_tickers:
            logger.warning("Stock monitoring not yet implemented, skipping: %s", stock_tickers)

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
                    await client.subscribe(crypto_tickers)
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

        old_price = self.price_state.get_price(update.ticker)

        # First price for this ticker - just record it
        if old_price is None:
            logger.info("Initial price for %s: $%.2f", monitor.name, update.price)
            self.price_state.set_price(update.ticker, update.price)
            return

        # Check if threshold is crossed
        alert = self._check_threshold(monitor, old_price, update.price)
        if alert:
            await self._send_alert(alert)
            # Update reference price after alert
            self.price_state.set_price(update.ticker, update.price)

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
            f"{emoji} {alert.monitor.name} {direction} "
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
