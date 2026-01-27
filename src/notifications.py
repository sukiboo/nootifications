from __future__ import annotations

import html
import logging
from typing import TYPE_CHECKING

from telegram import Bot
from telegram.error import TelegramError

from src.schemas import AlertInfo, AlertType, MonitorConfig

if TYPE_CHECKING:
    from src.utils import SettingsManager

logger = logging.getLogger(__name__)


def _format_alert(alert: AlertInfo, alert_type: AlertType) -> str:
    """Format alert message based on alert type."""
    direction = "up" if alert.change_pct > 0 else "down"
    status = "📈" if alert.change_pct > 0 else "📉"

    if alert_type == AlertType.PERCENT:
        return (
            f"{status} {alert.monitor.name} is {direction} "
            f"{abs(alert.change_pct):.2%} to ${alert.new_price:,.2f}"
        )
    elif alert_type == AlertType.INTERVAL:
        return f"{status} {alert.monitor.name} is {direction} to ${alert.new_price:,.2f}"
    elif alert_type == AlertType.TARGET:
        return f"🎯 {alert.monitor.name} reached target ${alert.monitor.target:,.2f}"
    else:
        return f"📊 {alert.monitor.name} price update: ${alert.new_price:,.2f}"


class TelegramNotifier:
    def __init__(self, bot_token: str, user_id: str) -> None:
        if not bot_token:
            raise ValueError("TELEGRAM_BOT_TOKEN is not set!")
        if not user_id:
            raise ValueError("TELEGRAM_USER_ID is not set!")

        self.bot = Bot(token=bot_token)
        self.user_id = user_id

    async def send(self, message: str, silent: bool = False) -> bool:
        """Send a monospace notification. Uses <code> + <br/> for newlines instead of
        <pre> so Telegram does not show the copy block UI, while keeping monospace
        and line breaks. Message content is HTML-escaped."""
        try:
            escaped = html.escape(message)
            for sep in ("\r\n", "\n", "\r"):
                escaped = escaped.replace(sep, "<br/>")
            await self.bot.send_message(
                chat_id=self.user_id,
                text=f"<code>{escaped}</code>",
                parse_mode="HTML",
                disable_notification=silent,
            )
            return True
        except TelegramError as e:
            logger.error("Failed to send Telegram message: %s", e)
            return False


class AlertHandler:
    """Handles price alert checks and notifications."""

    def __init__(self, notifier: TelegramNotifier, settings_manager: SettingsManager) -> None:
        self._notifier = notifier
        self._settings_manager = settings_manager

    @classmethod
    def create(cls, bot_token: str, user_id: str, config_path: str) -> "AlertHandler":
        """Factory method to create an AlertHandler with all dependencies."""
        from src.utils import SettingsManager

        notifier = TelegramNotifier(bot_token=bot_token, user_id=user_id)
        settings_manager = SettingsManager(config_path)
        return cls(notifier, settings_manager)

    async def notify_startup(self, bot_name: str, asset_count: int) -> None:
        """Send startup notification."""
        await self._notifier.send(f"🔆 {bot_name}: monitoring {asset_count} assets")

    async def notify_error(self, error: Exception) -> None:
        """Send error notification."""
        await self._notifier.send(f"❌ Bot crashed: {error}")

    async def check_and_notify(
        self, monitor: MonitorConfig, old_price: float, new_price: float
    ) -> bool:
        """Check all alert conditions and send notifications.

        Returns True if any alert was triggered.
        """
        triggered = False

        if alert := self._check_percent(monitor, old_price, new_price):
            await self._send(alert, AlertType.PERCENT)
            triggered = True

        if alert := self._check_interval(monitor, old_price, new_price):
            await self._send(alert, AlertType.INTERVAL)
            triggered = True

        if alert := self._check_target(monitor, old_price, new_price):
            await self._send(alert, AlertType.TARGET)
            self._settings_manager.mark_target_fired(monitor.ticker)
            monitor.fired = True
            triggered = True

        return triggered

    def _check_percent(
        self, monitor: MonitorConfig, old_price: float, new_price: float
    ) -> AlertInfo | None:
        """Check if price change exceeds percentage threshold."""
        if monitor.percent is None:
            return None

        change_pct = (new_price - old_price) / (old_price + 1e-9)
        if abs(change_pct) > monitor.percent:
            return AlertInfo(
                monitor=monitor,
                old_price=old_price,
                new_price=new_price,
                change_pct=change_pct,
            )
        return None

    def _check_interval(
        self, monitor: MonitorConfig, old_price: float, new_price: float
    ) -> AlertInfo | None:
        """Check if price crossed an interval boundary."""
        if monitor.interval is None:
            return None

        change_pct = (new_price - old_price) / (old_price + 1e-9)
        old_interval = round(old_price / monitor.interval)
        new_interval = round(new_price / monitor.interval)

        crossed = (
            new_price / monitor.interval > new_interval > old_interval
            or new_price / monitor.interval < new_interval < old_interval
        )

        if crossed:
            return AlertInfo(
                monitor=monitor,
                old_price=old_price,
                new_price=new_interval * monitor.interval,
                change_pct=change_pct,
            )
        return None

    def _check_target(
        self, monitor: MonitorConfig, old_price: float, new_price: float
    ) -> AlertInfo | None:
        """Check if price crossed the target (one-time alert)."""
        if monitor.target is None or monitor.fired:
            return None

        change_pct = (new_price - old_price) / (old_price + 1e-9)
        target = monitor.target

        crossed_up = old_price < target <= new_price
        crossed_down = old_price > target >= new_price

        if crossed_up or crossed_down:
            return AlertInfo(
                monitor=monitor,
                old_price=old_price,
                new_price=new_price,
                change_pct=change_pct,
            )
        return None

    async def _send(self, alert: AlertInfo, alert_type: AlertType) -> None:
        """Send an alert notification."""
        message = _format_alert(alert, alert_type)
        direction = "up" if alert.change_pct > 0 else "down"
        logger.info(
            "Alert [%s]: %s %s %.2f%% (%.2f -> %.2f)",
            alert_type.value,
            alert.monitor.name,
            direction,
            abs(alert.change_pct) * 100,
            alert.old_price,
            alert.new_price,
        )
        await self._notifier.send(message, silent=True)
