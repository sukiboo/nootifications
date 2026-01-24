#!/usr/bin/env python3

import asyncio
import logging
import sys

from src import NootificationsBot, Settings, setup_logger


def main() -> int:
    """Initialize and run the bot."""
    setup_logger(level=logging.INFO)
    logger = logging.getLogger(__name__)

    try:
        settings = Settings()
        logger.info("Configuration loaded: %s", settings.bot_name)
        logger.info("Monitoring %d assets", len(settings.app.assets))

        bot = NootificationsBot(settings)
        asyncio.run(bot.run())
        return 0

    except FileNotFoundError as e:
        logger.error("Configuration error: %s", e)
        return 1
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        return 0
    except Exception as e:
        logger.exception("Fatal error: %s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
