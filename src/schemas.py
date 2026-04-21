from __future__ import annotations

from enum import Enum
from typing import Self

from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings


class EnvSettings(BaseSettings):
    """Environment variables loaded from .env file."""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}
    telegram_bot_token: str = Field(..., description="Telegram bot API token")
    telegram_user_id: str = Field(..., description="Telegram user/chat ID for notifications")
    kraken_api_key: str | None = Field(default=None, description="Kraken API key")
    kraken_api_secret: str | None = Field(default=None, description="Kraken API secret")
    alpaca_api_key: str | None = Field(default=None, description="Alpaca API key")
    alpaca_api_secret: str | None = Field(default=None, description="Alpaca API secret")


class AppConfig(BaseModel):
    """Top-level application config loaded from settings.yaml."""

    bot_name: str = Field(default="nootifications-bot")
    clients: ClientsConfig = Field(default_factory=lambda: ClientsConfig())
    assets: list[MonitorConfig] = Field(default_factory=list)


class ClientsConfig(BaseModel):
    """Configuration for price data clients."""

    kraken: KrakenConfig = Field(default_factory=lambda: KrakenConfig())
    alpaca: AlpacaConfig = Field(default_factory=lambda: AlpacaConfig())


class KrakenConfig(BaseModel):
    """Configuration for Kraken WebSocket client."""

    throttle_seconds: float = Field(
        default=1.0, description="Min seconds between price updates per ticker"
    )
    smoothing: float = Field(
        default=0.0, ge=0, lt=1, description="Price smoothing factor (0=disabled, 0.9=heavy)"
    )
    reconnect_delay: int = Field(default=5, description="Seconds between reconnection attempts")
    max_reconnect_attempts: int = Field(
        default=10, description="Max reconnection attempts before giving up"
    )
    stale_timeout_seconds: int = Field(
        default=600, description="Force reconnect if no updates received in this many seconds"
    )


class AlpacaConfig(BaseModel):
    """Configuration for Alpaca WebSocket client (US stocks)."""

    feed: str = Field(
        default="iex",
        description="Market data feed: 'iex' (free) or 'sip'",
    )
    throttle_seconds: float = Field(
        default=1.0, description="Min seconds between price updates per ticker"
    )
    smoothing: float = Field(
        default=0.0, ge=0, lt=1, description="Price smoothing factor (0=disabled, 0.9=heavy)"
    )
    reconnect_delay: int = Field(default=5, description="Seconds between reconnection attempts")
    max_reconnect_attempts: int = Field(
        default=10, description="Max reconnection attempts before giving up"
    )
    stale_timeout_seconds: int = Field(
        default=600, description="Force reconnect if no updates received in this many seconds"
    )


class Client(str, Enum):
    """Supported price data clients."""

    KRAKEN = "kraken"
    ALPACA = "alpaca"


class MonitorConfig(BaseModel):
    """Configuration for a single asset to monitor (from settings.yaml)."""

    name: str = Field(..., description="Display name for notifications")
    client: Client = Field(..., description="Client to use for price data")
    ticker: str = Field(..., description="Ticker symbol in client's format")
    percent: float | None = Field(
        default=None, gt=0, lt=1, description="Percentage change threshold (e.g., 0.01 for 1%)"
    )
    interval: float | None = Field(
        default=None, gt=0, description="Price interval for crossing alerts (e.g., 1000 for $1000)"
    )
    target: float | None = Field(
        default=None, gt=0, description="One-time price target alert (e.g., 300 for $300)"
    )
    fired: bool = Field(default=False, description="Whether target alert has fired (set by bot)")

    @model_validator(mode="after")
    def at_least_one_alert(self) -> Self:
        if not any([self.percent, self.interval, self.target]):
            raise ValueError("At least one alert type required: percent, interval, or target")
        return self


class PriceState(BaseModel):
    """Persisted price state for crash recovery (logs/prices_log.json)."""

    prices: dict[str, float] = Field(default_factory=dict)

    def get_price(self, ticker: str) -> float | None:
        return self.prices.get(ticker)

    def set_price(self, ticker: str, price: float) -> None:
        self.prices[ticker] = price


class PriceUpdate(BaseModel):
    """A price update event received from a WebSocket client."""

    ticker: str
    price: float
    source: str = Field(default="unknown", description="Source client identifier")


class PriceContext(BaseModel):
    """Context for checking price alerts, computed once per price update."""

    monitor: MonitorConfig
    old_price: float
    new_price: float
    change_pct: float

    @classmethod
    def create(cls, monitor: MonitorConfig, old_price: float, new_price: float) -> "PriceContext":
        """Create a PriceContext with computed change_pct."""
        change_pct = (new_price - old_price) / (old_price + 1e-9)
        return cls(monitor=monitor, old_price=old_price, new_price=new_price, change_pct=change_pct)

    def to_alert(self, display_price: float | None = None) -> "AlertInfo":
        """Convert to AlertInfo, optionally with a display price override (for interval alerts)."""
        return AlertInfo(ctx=self, display_price=display_price)


class AlertInfo(BaseModel):
    """Data for a triggered price alert, passed to notification sender."""

    ctx: PriceContext
    display_price: float | None = None

    @property
    def monitor(self) -> MonitorConfig:
        return self.ctx.monitor

    @property
    def old_price(self) -> float:
        return self.ctx.old_price

    @property
    def new_price(self) -> float:
        return self.display_price if self.display_price is not None else self.ctx.new_price

    @property
    def change_pct(self) -> float:
        return self.ctx.change_pct


class AlertType(Enum):
    """Types of price alerts."""

    PERCENT = "percent"
    INTERVAL = "interval"
    TARGET = "target"
