"""Tests for price alert logic - the core functionality."""

from unittest.mock import MagicMock

import pytest

from src.notifications.alerts import AlertHandler
from src.schemas import Client, MonitorConfig, PriceContext


class TestPercentAlerts:
    """Percentage-based price change alerts."""

    def test_triggers_when_above_threshold(
        self, alert_handler: AlertHandler, percent_monitor: MonitorConfig
    ) -> None:
        """2% move should trigger 1% threshold."""
        ctx = PriceContext.create(percent_monitor, old_price=100.0, new_price=102.0)
        assert alert_handler._check_percent(ctx) is not None

    def test_triggers_on_decrease(
        self, alert_handler: AlertHandler, percent_monitor: MonitorConfig
    ) -> None:
        """2% drop should also trigger."""
        ctx = PriceContext.create(percent_monitor, old_price=100.0, new_price=98.0)
        assert alert_handler._check_percent(ctx) is not None

    def test_no_trigger_below_threshold(
        self, alert_handler: AlertHandler, percent_monitor: MonitorConfig
    ) -> None:
        """0.5% move should NOT trigger 1% threshold."""
        ctx = PriceContext.create(percent_monitor, old_price=100.0, new_price=100.5)
        assert alert_handler._check_percent(ctx) is None

    def test_no_trigger_at_exactly_threshold(
        self, alert_handler: AlertHandler, percent_monitor: MonitorConfig
    ) -> None:
        """Exactly 1% should NOT trigger (must exceed)."""
        ctx = PriceContext.create(percent_monitor, old_price=100.0, new_price=101.0)
        assert alert_handler._check_percent(ctx) is None


class TestIntervalAlerts:
    """Interval boundary crossing alerts (e.g., BTC crossing $100k)."""

    def test_triggers_on_boundary_cross_up(
        self, alert_handler: AlertHandler, interval_monitor: MonitorConfig
    ) -> None:
        """Crossing from 99.4k to 100.1k should trigger $100k alert."""
        # The algorithm: old_idx=round(99.4)=99, new_idx=round(100.1)=100, ratio=100.1
        # crossed = (100.1 > 100 > 99) = True
        ctx = PriceContext.create(interval_monitor, old_price=99400, new_price=100100)
        alert = alert_handler._check_interval(ctx)

        assert alert is not None
        assert alert.new_price == 100000  # Display price is the boundary

    def test_triggers_on_boundary_cross_down(
        self, alert_handler: AlertHandler, interval_monitor: MonitorConfig
    ) -> None:
        """Crossing from 100.6k to 99.9k should trigger $100k alert."""
        ctx = PriceContext.create(interval_monitor, old_price=100600, new_price=99900)
        alert = alert_handler._check_interval(ctx)

        assert alert is not None
        assert alert.new_price == 100000

    def test_no_trigger_within_same_interval(
        self, alert_handler: AlertHandler, interval_monitor: MonitorConfig
    ) -> None:
        """Moving within the same $1k band should NOT trigger."""
        ctx = PriceContext.create(interval_monitor, old_price=100200, new_price=100800)
        assert alert_handler._check_interval(ctx) is None


class TestTargetAlerts:
    """One-time target price alerts."""

    @pytest.mark.asyncio
    async def test_triggers_on_crossing_target(
        self, alert_handler: AlertHandler, target_monitor: MonitorConfig
    ) -> None:
        """Crossing $100k target should trigger."""
        ctx = PriceContext.create(target_monitor, old_price=99000, new_price=101000)
        alert = await alert_handler._check_target(ctx)

        assert alert is not None
        assert target_monitor.fired is True  # Should mark as fired

    @pytest.mark.asyncio
    async def test_triggers_on_crossing_down(
        self, alert_handler: AlertHandler, target_monitor: MonitorConfig
    ) -> None:
        """Crossing target going DOWN should also trigger."""
        ctx = PriceContext.create(target_monitor, old_price=101000, new_price=99000)
        assert await alert_handler._check_target(ctx) is not None

    @pytest.mark.asyncio
    async def test_no_trigger_without_crossing(
        self, alert_handler: AlertHandler, target_monitor: MonitorConfig
    ) -> None:
        """Staying below target should NOT trigger."""
        ctx = PriceContext.create(target_monitor, old_price=95000, new_price=99000)
        assert await alert_handler._check_target(ctx) is None

    @pytest.mark.asyncio
    async def test_only_fires_once(self, alert_handler: AlertHandler) -> None:
        """Target should NOT fire again after already fired."""
        monitor = MonitorConfig(
            name="BTC",
            client=Client.KRAKEN,
            ticker="BTC/USD",
            target=100000,
            fired=True,  # Already fired
        )
        ctx = PriceContext.create(monitor, old_price=99000, new_price=101000)
        assert await alert_handler._check_target(ctx) is None

    @pytest.mark.asyncio
    async def test_marks_correct_target_when_multiple_exist(
        self, alert_handler: AlertHandler, mock_settings_manager: MagicMock
    ) -> None:
        """When same ticker has multiple targets, only the crossed target is marked fired."""
        monitor_250 = MonitorConfig(name="Apple", client=Client.ALPACA, ticker="AAPL", target=250)
        monitor_275 = MonitorConfig(name="Apple", client=Client.ALPACA, ticker="AAPL", target=275)

        # Price crosses 250 but not 275
        ctx_250 = PriceContext.create(monitor_250, old_price=245, new_price=255)
        ctx_275 = PriceContext.create(monitor_275, old_price=245, new_price=255)

        await alert_handler._check_target(ctx_250)
        await alert_handler._check_target(ctx_275)

        # Should only mark the 250 target as fired (with ticker AND target)
        mock_settings_manager.mark_target_fired.assert_called_once_with("AAPL", 250)


class TestCheckAndNotify:
    """Integration test for the full check_and_notify flow."""

    @pytest.mark.asyncio
    async def test_sends_notification_on_alert(
        self,
        alert_handler: AlertHandler,
        mock_notifier: MagicMock,
        percent_monitor: MonitorConfig,
    ) -> None:
        """Should send notification when alert triggers."""
        result = await alert_handler.check_and_notify(percent_monitor, 100.0, 105.0)

        assert result is True
        mock_notifier.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_notification_when_no_alert(
        self,
        alert_handler: AlertHandler,
        mock_notifier: MagicMock,
        percent_monitor: MonitorConfig,
    ) -> None:
        """Should NOT send notification when no alert."""
        result = await alert_handler.check_and_notify(percent_monitor, 100.0, 100.5)

        assert result is False
        mock_notifier.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_multiple_alerts_send_multiple_notifications(
        self, alert_handler: AlertHandler, mock_notifier: MagicMock
    ) -> None:
        """Monitor with multiple alert types should send multiple notifications."""
        monitor = MonitorConfig(
            name="BTC",
            client=Client.KRAKEN,
            ticker="BTC/USD",
            percent=0.01,  # 1%
            interval=1000,
        )
        # Big move: ~2% change AND crosses a $1k boundary
        # 98000 -> 100100 = 2.1% change, crosses $100k
        result = await alert_handler.check_and_notify(monitor, 98000, 100100)

        assert result is True
        assert mock_notifier.send.call_count == 2
