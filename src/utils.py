import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml
from ruamel.yaml import YAML

from src.schemas import AppConfig, EnvSettings, PriceState


class UTCFormatter(logging.Formatter):
    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        ct = time.gmtime(record.created)
        if datefmt:
            return time.strftime(datefmt, ct) + "Z"
        return time.strftime("%Y-%m-%d %H:%M:%S", ct) + "Z"


def setup_logger(
    name: str = "",
    log_dir: str = "logs",
    level: int = logging.INFO,
    console_level: int | None = None,
) -> logging.Logger:
    """
    Configure and return a logger with file and console handlers.

    Args:
        name: Logger name (empty string for root logger)
        log_dir: Directory for log files
        level: Logging level for file handler
        console_level: Logging level for console (defaults to same as level)

    Returns:
        Configured logger
    """
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    log_file = Path(log_dir) / f"{datetime.now(timezone.utc).strftime('%Y-%m')}.log"

    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    formatter = UTCFormatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # File handler
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(console_level or level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # Silence noisy third-party loggers
    for lib in ["telegram", "telegram.ext", "httpx", "httpcore", "aiohttp", "websockets"]:
        logging.getLogger(lib).setLevel(logging.WARNING)

    return logger


class Settings:
    """Unified settings manager for app config and environment variables."""

    def __init__(self, config_path: str | Path = "settings.yaml") -> None:
        self.env = EnvSettings()  # type: ignore[call-arg]
        self.app = self._load_yaml_config(config_path)

    def _load_yaml_config(self, config_path: str | Path) -> AppConfig:
        """Load and validate the YAML configuration file."""
        path = Path(config_path)
        if not path.exists():
            raise FileNotFoundError(f"Configuration file not found: {config_path}")

        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        return AppConfig(**data)

    @property
    def bot_name(self) -> str:
        return self.app.bot_name


class PriceStateManager:
    """Manages persistent price state for crash recovery."""

    def __init__(self, state_file: str | Path = "logs/prices_log.json") -> None:
        self.state_file = Path(state_file)
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state = self._load_state()

    def _load_state(self) -> PriceState:
        """Load state from disk or create empty state."""
        if self.state_file.exists():
            try:
                with open(self.state_file, encoding="utf-8") as f:
                    data = json.load(f)
                return PriceState(**data)
            except (json.JSONDecodeError, ValueError):
                pass
        return PriceState()

    def save(self) -> None:
        """Persist current state to disk."""
        with open(self.state_file, "w", encoding="utf-8") as f:
            json.dump(self.state.model_dump(), f, indent=2)

    def get_price(self, ticker: str) -> float | None:
        """Get last recorded price for a ticker."""
        return self.state.get_price(ticker)

    def set_price(self, ticker: str, price: float) -> None:
        """Update price for a ticker and persist."""
        self.state.set_price(ticker, price)
        self.save()


class SettingsManager:
    """Manages writing updates back to settings.yaml (e.g., marking targets as fired)."""

    def __init__(self, settings_path: str | Path = "settings.yaml") -> None:
        self.settings_path = Path(settings_path)
        self._yaml = YAML()
        self._yaml.preserve_quotes = True
        self._yaml.indent(mapping=2, sequence=4, offset=2)

    def mark_target_fired(self, ticker: str, target: float) -> None:
        """Mark a target as fired by adding 'fired: true' to the asset in settings.yaml."""
        if not self.settings_path.exists():
            logging.warning("Settings file not found: %s", self.settings_path)
            return

        # Load with ruamel.yaml to preserve formatting and comments
        with open(self.settings_path, encoding="utf-8") as f:
            data = self._yaml.load(f)

        # Find the asset by ticker and target, then add fired: true
        assets = data.get("assets", [])
        for asset in assets:
            if asset.get("ticker") == ticker and asset.get("target") == target:
                asset["fired"] = True
                break
        else:
            logging.warning(
                "Asset with ticker %s and target %s not found in settings", ticker, target
            )
            return

        # Write back preserving formatting
        with open(self.settings_path, "w", encoding="utf-8") as f:
            self._yaml.dump(data, f)
