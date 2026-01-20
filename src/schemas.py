from enum import Enum

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings


class AssetType(str, Enum):
    CRYPTO = "crypto"
    STOCK = "stock"  # Future support


class MonitorConfig(BaseModel):
    name: str = Field(..., description="Display name for notifications")
    type: AssetType = Field(..., description="Asset type (crypto, stock)")
    ticker: str = Field(..., description="Exchange ticker symbol")
    delta: float = Field(
        ...,
        gt=0,
        description=(
            "Price change threshold: either percentage (if <1)"
            " or absolute dollar amount (if >=1)"
        ),
    )

    @property
    def is_percentage(self) -> bool:
        return self.delta < 1

    def format_delta(self) -> str:
        if self.is_percentage:
            return f"{self.delta * 100:.1f}%"
        else:
            return f"${self.delta:,.2f}"


class AppConfig(BaseModel):
    bot_name: str = Field(default="nootifications-bot")
    monitoring: list[MonitorConfig] = Field(default_factory=list)


class EnvSettings(BaseSettings):
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}
    telegram_bot_token: str = Field(..., description="Telegram bot API token")
    telegram_user_id: str = Field(..., description="Telegram user/chat ID for notifications")
    kraken_api_key: str | None = Field(default=None, description="Kraken API key")
    kraken_api_secret: str | None = Field(default=None, description="Kraken API secret")


class PriceState(BaseModel):
    prices: dict[str, float] = Field(default_factory=dict)

    def get_price(self, ticker: str) -> float | None:
        return self.prices.get(ticker)

    def set_price(self, ticker: str, price: float) -> None:
        self.prices[ticker] = price


class PriceUpdate(BaseModel):
    ticker: str
    price: float
    source: str = Field(default="unknown", description="Source client identifier")
