import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api import router
from app.bot import create_bot
from app.config import DATA_DIR, TELEGRAM_BOT_TOKEN, UPLOAD_DIR
from app.database import init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

bot_task: asyncio.Task | None = None


async def run_bot() -> None:
    if not TELEGRAM_BOT_TOKEN:
        logger.warning("TELEGRAM_BOT_TOKEN not set — bot disabled")
        return

    bot, dp = create_bot()
    logger.info("Telegram bot started")
    await dp.start_polling(bot)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global bot_task

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    await init_db()

    if TELEGRAM_BOT_TOKEN:
        bot_task = asyncio.create_task(run_bot())

    yield

    if bot_task:
        bot_task.cancel()
        try:
            await bot_task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="Tank Creative Bot", lifespan=lifespan)
app.include_router(router)

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")
