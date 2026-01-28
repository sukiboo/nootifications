import html
import logging

from telegram import Bot
from telegram.error import TelegramError

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """Handles sending notifications via Telegram."""

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
