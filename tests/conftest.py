"""Fixtures for alert testing."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.notifications.alerts import AlertHandler
from src.notifications.telegram import TelegramNotifier
from src.schemas import Client, MonitorConfig
from src.utils import SettingsManager


@pytest.fixture
def mock_notifier() -> MagicMock:
    """Mock TelegramNotifier - we don't need to test actual Telegram sends."""
    notifier = MagicMock(spec=TelegramNotifier)
    notifier.send = AsyncMock(return_value=True)
    return notifier


@pytest.fixture
def mock_settings_manager() -> MagicMock:
    """Mock SettingsManager - we don't need to test YAML file writes."""
    return MagicMock(spec=SettingsManager)


@pytest.fixture
def alert_handler(mock_notifier: MagicMock, mock_settings_manager: MagicMock) -> AlertHandler:
    """AlertHandler with mocked dependencies."""
    return AlertHandler(notifier=mock_notifier, settings_manager=mock_settings_manager)


@pytest.fixture
def percent_monitor() -> MonitorConfig:
    """Monitor with 1% threshold."""
    return MonitorConfig(
        name="Bitcoin",
        client=Client.KRAKEN,
        ticker="BTC/USD",
        percent=0.01,
    )


@pytest.fixture
def interval_monitor() -> MonitorConfig:
    """Monitor with $1000 interval."""
    return MonitorConfig(
        name="Bitcoin",
        client=Client.KRAKEN,
        ticker="BTC/USD",
        interval=1000,
    )


@pytest.fixture
def target_monitor() -> MonitorConfig:
    """Monitor with $100k target."""
    return MonitorConfig(
        name="Bitcoin",
        client=Client.KRAKEN,
        ticker="BTC/USD",
        target=100000,
    )
