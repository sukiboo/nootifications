#!/usr/bin/env python3

import asyncio
import logging
import signal
import sys

from src import NootificationsBot, Settings, setup_logger


async def run_bot(settings: Settings) -> None:
    """Run the bot with signal handling for graceful shutdown."""
    loop = asyncio.get_running_loop()
    shutdown_event = asyncio.Event()

    def handle_signal() -> None:
        shutdown_event.set()

    # Register handlers for both SIGINT (Ctrl+C) and SIGTERM (Docker stop)
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, handle_signal)

    bot = NootificationsBot(settings)
    bot_task = asyncio.create_task(bot.run())

    # Wait for either the bot to finish or a shutdown signal
    shutdown_task = asyncio.create_task(shutdown_event.wait())
    done, pending = await asyncio.wait(
        [bot_task, shutdown_task], return_when=asyncio.FIRST_COMPLETED
    )

    # If shutdown signal received, cancel the bot
    if shutdown_event.is_set() and not bot_task.done():
        bot_task.cancel()
        try:
            await bot_task
        except asyncio.CancelledError:
            pass

    # Cancel any remaining tasks
    for task in pending:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


def main() -> int:
    """Initialize and run the bot."""
    setup_logger(level=logging.INFO)
    logger = logging.getLogger(__name__)

    try:
        settings = Settings()
        logger.info("Configuration loaded: %s", settings.bot_name)
        logger.info("Monitoring %d assets", len(settings.app.assets))

        asyncio.run(run_bot(settings))
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
