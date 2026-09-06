import logging
from aiogram import Bot, Dispatcher
from config import config
from bot.handlers import router

logger = logging.getLogger("AdminBot")


async def run_admin_bot():
    """Start the aiogram admin bot polling."""
    if not config.BOT_TOKEN:
        logger.warning("BOT_TOKEN is not set. Admin Bot will be disabled.")
        return

    bot = Bot(token=config.BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)

    logger.info("Telegram Admin Bot started polling.")
    try:
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"Error in Admin Bot: {e}")
    finally:
        await bot.session.close()
