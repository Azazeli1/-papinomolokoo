import logging

from aiogram import Bot

from app.config import TELEGRAM_BOT_TOKEN

logger = logging.getLogger(__name__)

_bot: Bot | None = None


def get_bot() -> Bot | None:
    global _bot
    if not TELEGRAM_BOT_TOKEN:
        return None
    if _bot is None:
        _bot = Bot(token=TELEGRAM_BOT_TOKEN)
    return _bot


async def notify_buyer(telegram_id: int, text: str) -> None:
    bot = get_bot()
    if not bot:
        return
    try:
        await bot.send_message(telegram_id, text, parse_mode="HTML")
    except Exception:
        logger.exception("Failed to notify buyer %s", telegram_id)
