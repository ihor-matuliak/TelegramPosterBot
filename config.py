import os
from pathlib import Path
from typing import List
from dotenv import load_dotenv

# Load environment variables from .env
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


class Config:
    # Telegram MTProto Userbot
    TELEGRAM_API_ID: int = int(os.getenv("TELEGRAM_API_ID", "0"))
    TELEGRAM_API_HASH: str = os.getenv("TELEGRAM_API_HASH", "")
    TELEGRAM_SESSION_NAME: str = os.getenv("TELEGRAM_SESSION_NAME", "poster_session")

    # Telegram Admin Bot
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    _ADMIN_IDS_RAW: str = os.getenv("ADMIN_USER_IDS", "")
    
    @classmethod
    def get_admin_ids(cls) -> List[int]:
        if not cls._ADMIN_IDS_RAW:
            return []
        try:
            return [int(x.strip()) for x in cls._ADMIN_IDS_RAW.split(",") if x.strip()]
        except ValueError:
            return []

    # Google Gemini AI
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")

    # Supabase Cloud Database
    SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
    SUPABASE_KEY: str = os.getenv("SUPABASE_KEY", "")

    # Web Dashboard
    WEB_HOST: str = os.getenv("WEB_HOST", "127.0.0.1")
    WEB_PORT: int = int(os.getenv("WEB_PORT", "8000"))


config = Config()
