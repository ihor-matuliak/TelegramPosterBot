import os
import asyncio
import logging
from telethon import TelegramClient
from telethon.tl.types import (
    MessageEntityCustomEmoji,
    SendMessageTypingAction
)
from telethon.tl.functions.messages import SetTypingRequest
from telethon.extensions import html
from config import config, BASE_DIR

logger = logging.getLogger("TelegramClient")

SESSION_PATH = str(BASE_DIR / config.TELEGRAM_SESSION_NAME)

client = TelegramClient(
    SESSION_PATH,
    api_id=config.TELEGRAM_API_ID,
    api_hash=config.TELEGRAM_API_HASH,
    device_model="Desktop PC",
    system_version="Windows 11",
    app_version="5.4.1"
)


async def init_telegram_client() -> TelegramClient:
    """Initialize and connect the Telethon MTProto client."""
    if not client.is_connected():
        logger.info("Connecting to Telegram MTProto...")
        await client.connect()
    
    if not await client.is_user_authorized():
        logger.warning("Telegram client is NOT authorized yet! Please run login authorization.")
    else:
        me = await client.get_me()
        logger.info(f"Telegram client connected as: {me.first_name} (@{me.username or 'no_username'}) [ID: {me.id}]")
    
    return client


async def get_latest_saved_message() -> dict:
    """
    Fetch the latest message from 'Saved Messages' (me).
    Extracts text, HTML, message_id, and entity metadata.
    """
    if not client.is_connected() or not await client.is_user_authorized():
        return {"status": "error", "message": "Telegram клієнт не авторизований"}

    messages = await client.get_messages("me", limit=1)
    if not messages:
        return {"status": "error", "message": "У ваших 'Збережених' немає жодного повідомлення"}

    msg = messages[0]
    raw_text = msg.text or msg.message or ""
    
    # Telethon HTML representation
    html_text = html.unparse(raw_text, msg.entities) if msg.entities else raw_text

    return {
        "status": "ok",
        "message_id": msg.id,
        "raw_text": raw_text,
        "html_text": html_text,
        "entities_count": len(msg.entities) if msg.entities else 0,
        "has_custom_emojis": any(isinstance(e, MessageEntityCustomEmoji) for e in (msg.entities or []))
    }


async def get_source_message(chat_peer: str = "me", message_id: int | None = None):
    """Retrieve raw message with all original entities from Telegram."""
    if not client.is_connected() or not await client.is_user_authorized() or not message_id:
        return None
    try:
        msgs = await client.get_messages(chat_peer, ids=message_id)
        return msgs if msgs else None
    except Exception as e:
        logger.warning(f"Could not fetch source message {message_id} from {chat_peer}: {e}")
        return None


async def simulate_typing(peer, duration_seconds: int = 4):
    """Simulate human typing in the target chat before sending."""
    try:
        await client(SetTypingRequest(peer=peer, action=SendMessageTypingAction()))
        await asyncio.sleep(duration_seconds)
    except Exception:
        pass

