import logging

from src.notifications.telegram import TelegramNotifier
from src.schemas import AlertInfo, AlertType, MonitorConfig, PriceContext
from src.utils import SettingsManager

logger = logging.getLogger(__name__)


class AlertHandler:
    """Handles price alert checks and notifications."""

    def __init__(self, notifier: TelegramNotifier, settings_manager: SettingsManager) -> None:
        self._notifier = notifier
        self._settings_manager = settings_manager

    @classmethod
    def create(cls, bot_token: str, user_id: str, config_path: str) -> "AlertHandler":
        """Factory method to create an AlertHandler with all dependencies."""
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
        """Check alert conditions and send notifications. Return True if an alert was triggered."""
        ctx = PriceContext.create(monitor, old_price, new_price)
        alerts: list[tuple[AlertInfo, AlertType]] = []

        if alert := self._check_percent(ctx):
            alerts.append((alert, AlertType.PERCENT))
        if alert := self._check_interval(ctx):
            alerts.append((alert, AlertType.INTERVAL))
        if alert := self._check_target(ctx):
            alerts.append((alert, AlertType.TARGET))

        for alert, alert_type in alerts:
            await self._send(alert, alert_type)

        return bool(alerts)

    def _check_percent(self, ctx: PriceContext) -> AlertInfo | None:
        """Check if price change exceeds percentage threshold."""
        if ctx.monitor.percent and abs(ctx.change_pct) > ctx.monitor.percent:
            return ctx.to_alert()
        else:
            return None

    def _check_interval(self, ctx: PriceContext) -> AlertInfo | None:
        """Check if price crossed an interval boundary."""
        if not ctx.monitor.interval:
            return None
        else:
            old_idx = round(ctx.old_price / ctx.monitor.interval)
            new_idx = round(ctx.new_price / ctx.monitor.interval)
            ratio = ctx.new_price / ctx.monitor.interval
            crossed = ((ratio > new_idx > old_idx) or (ratio < new_idx < old_idx))  # fmt: skip
            if crossed:
                return ctx.to_alert(display_price=new_idx * ctx.monitor.interval)
            else:
                return None

    def _check_target(self, ctx: PriceContext) -> AlertInfo | None:
        """Check if price crossed the target (one-time alert)."""
        if not ctx.monitor.target or ctx.monitor.fired:
            return None
        else:
            crossed = (
                (ctx.old_price < ctx.monitor.target < ctx.new_price)
                or (ctx.old_price > ctx.monitor.target > ctx.new_price)
            )  # fmt: skip
            if crossed:
                self._settings_manager.mark_target_fired(ctx.monitor.ticker)
                ctx.monitor.fired = True
                return ctx.to_alert()
            else:
                return None

    async def _send(self, alert: AlertInfo, alert_type: AlertType) -> None:
        """Send an alert notification."""
        message = self._format_alert(alert, alert_type)
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

    def _format_alert(self, alert: AlertInfo, alert_type: AlertType) -> str:
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
            return f"🎯 {alert.monitor.name} reached ${alert.monitor.target:,.2f}"
        else:
            return f"📊 {alert.monitor.name} price update: ${alert.new_price:,.2f}"
