import asyncio
import logging
import sys
import uvicorn
from config import config
from core.client import init_telegram_client
from core.poster import poster_worker
from web.api import web_app
from bot.admin_bot import run_admin_bot

# ASGI app instance (required by platforms like Vercel)
app = web_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] (%(name)s) %(message)s",
    stream=sys.stdout
)
logger = logging.getLogger("MainOrchestrator")


async def run_fastapi():
    """Run the FastAPI web dashboard."""
    server_config = uvicorn.Config(
        app=web_app,
        host=config.WEB_HOST,
        port=config.WEB_PORT,
        log_level="info"
    )
    server = uvicorn.Server(server_config)
    logger.info(f"🌐 Desktop Web Dashboard running at: http://{config.WEB_HOST}:{config.WEB_PORT}")
    await server.serve()


async def main():
    logger.info("=" * 60)
    logger.info("🚀 Запуск комплексу Telegram Auto-Poster...")
    logger.info("=" * 60)

    # 1. Initialize Telegram Userbot client
    try:
        await init_telegram_client()
    except Exception as e:
        logger.error(f"Помилка ініціалізації Telegram клієнта: {e}")

    # 2. Start Poster Worker Engine
    await poster_worker.start()

    # 3. Launch Web Dashboard, Admin Bot, and Worker concurrently
    try:
        await asyncio.gather(
            run_fastapi(),
            run_admin_bot(),
            return_exceptions=True
        )
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Зупинка системи...")
    finally:
        await poster_worker.stop()
        logger.info("Всі процеси коректно зупинено.")


if __name__ == "__main__":
    asyncio.run(main())
