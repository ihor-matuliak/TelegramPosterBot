import asyncio
import logging
import re
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import List, Optional
from pathlib import Path
from telethon.errors import (
    FloodWaitError,
    PhoneNumberInvalidError,
    PhoneCodeInvalidError,
    PhoneCodeExpiredError,
    SessionPasswordNeededError,
    PasswordHashInvalidError,
    PhoneNumberBannedError
)
from database.client import db
from core.client import (
    client,
    init_telegram_client,
    reset_client_session,
    logout_client,
    login_with_bot_token,
    get_latest_saved_message
)

try:
    from core.client import normalize_phone, describe_sent_code_type
except ImportError:
    normalize_phone = None
    describe_sent_code_type = None

if not normalize_phone:
    def normalize_phone(raw: str) -> str:
        clean = "".join(ch for ch in str(raw).strip() if ch.isdigit() or ch == "+")
        digits = clean[1:] if clean.startswith("+") else clean
        if not digits:
            return ""
        if len(digits) == 10 and digits.startswith("0"):
            return f"+38{digits}"
        if len(digits) == 12 and digits.startswith("380"):
            return f"+{digits}"
        if len(digits) == 11 and digits.startswith("80"):
            return f"+3{digits}"
        return f"+{digits}"

if not describe_sent_code_type:
    def describe_sent_code_type(sent_code_type) -> str:
        type_name = type(sent_code_type).__name__ if sent_code_type else ""
        if "App" in type_name:
            return "у додаток Telegram (офіційний системний чат «Telegram» з синьою галочкою від сервісу 777000)"
        elif "Sms" in type_name:
            return "у звичайне SMS-повідомлення на ваш телефонний номер"
        return "у додаток Telegram (офіційний системний чат «Telegram»)"

from core.spintax import SpintaxEngine
from core.finder import ChatFinder
from core.poster import poster_worker
from core.ai_discovery import AIDiscoveryEngine
from web.discovery_api import router as discovery_router, manager as discovery_manager
from database.discovery import DiscoveryStorageError

logger = logging.getLogger("WebAPI")
_pending_auth: dict[str, str] = {}

@asynccontextmanager
async def lifespan(app: FastAPI):
    await discovery_manager.restore()
    try:
        yield
    finally:
        await discovery_manager.close()


web_app = FastAPI(title="Telegram Auto-Poster Dashboard", lifespan=lifespan)
web_app.include_router(discovery_router)


@web_app.exception_handler(DiscoveryStorageError)
async def discovery_storage_error(request, exc: DiscoveryStorageError):
    return JSONResponse(status_code=503, content={"detail": str(exc)})

STATIC_DIR = Path(__file__).resolve().parent / "static"
web_app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# Pydantic Schemas
class ChatCreateRequest(BaseModel):
    chat_peer: str
    interval_minutes: int = 60
    title: Optional[str] = ""
    post_id: Optional[str] = None

class BatchChatsRequest(BaseModel):
    text_links: str
    interval_minutes: int = 60
    post_id: Optional[str] = None

class ChatUpdateRequest(BaseModel):
    interval_minutes: Optional[int] = None
    is_active: Optional[bool] = None
    title: Optional[str] = None
    reset_status: Optional[bool] = False
    post_id: Optional[str] = None

class PostSaveRequest(BaseModel):
    content: str
    title: Optional[str] = "Головне оголошення"
    post_id: Optional[str] = None
    source_msg_id: Optional[int] = None
    source_chat_peer: Optional[str] = "me"
    is_active: Optional[bool] = False

class SyncSavedPostRequest(BaseModel):
    post_id: Optional[str] = None
    create_new: Optional[bool] = False

class SpintaxPreviewRequest(BaseModel):
    text: str

class SearchChatsRequest(BaseModel):
    query: str
    limit: Optional[int] = 40

class JoinAddChatRequest(BaseModel):
    chat_peer: str
    interval_minutes: int = 60
    title: Optional[str] = ""
    post_id: Optional[str] = None

class BatchJoinAddRequest(BaseModel):
    items: List[JoinAddChatRequest]

class SettingsUpdateRequest(BaseModel):
    is_running: Optional[bool] = None
    min_delay_seconds: Optional[int] = None
    max_delay_seconds: Optional[int] = None
    jitter_minutes: Optional[int] = None
    max_posts_per_hour: Optional[int] = None
    max_posts_per_day: Optional[int] = None
    batch_size: Optional[int] = None
    batch_rest_minutes: Optional[int] = None
    enable_typing_simulation: Optional[bool] = None
    typing_duration_seconds: Optional[int] = None
    enable_spintax: Optional[bool] = None
    enable_anti_fingerprint: Optional[bool] = None
    enable_night_mode: Optional[bool] = None
    night_start_hour: Optional[int] = None
    night_end_hour: Optional[int] = None
    auto_circuit_breaker: Optional[bool] = None


class PhoneAuthRequest(BaseModel):
    phone: str

class VerifyCodeRequest(BaseModel):
    phone: str
    code: str
    phone_code_hash: Optional[str] = None

class Verify2FARequest(BaseModel):
    password: str

class BotTokenAuthRequest(BaseModel):
    bot_token: Optional[str] = None


@web_app.get("/")
async def serve_index():
    return FileResponse(STATIC_DIR / "index.html")


@web_app.get("/api/status")
async def get_system_status():
    is_authorized = False
    user_info = None
    try:
        if not client.is_connected():
            await init_telegram_client()
        if client.is_connected() and await client.is_user_authorized():
            is_authorized = True
            me = await client.get_me()
            user_info = {
                "id": me.id,
                "name": f"{me.first_name} {me.last_name or ''}".strip(),
                "username": me.username,
                "phone": me.phone,
                "is_premium": getattr(me, "premium", False)
            }
    except Exception as e:
        logger.warning(f"Error during Telegram client status check: {e}")

    settings = db.get_settings()
    chats = db.get_all_chats()
    active_chats = sum(1 for c in chats if c.get("is_active"))
    error_chats = sum(1 for c in chats if c.get("status") in ["error", "restricted"])
    hourly_count = db.get_hourly_post_count()
    daily_count = db.get_daily_post_count()

    return {
        "is_authorized": is_authorized,
        "user": user_info,
        "is_running": settings.get("is_running", False),
        "poster": poster_worker.get_status(settings, is_authorized, hourly_count, daily_count),
        "total_chats": len(chats),
        "active_chats": active_chats,
        "error_chats": error_chats,
        "hourly_posts": hourly_count,
        "daily_posts": daily_count,
        "settings": settings
    }


@web_app.post("/api/settings/toggle")
async def toggle_poster():
    settings = db.get_settings()
    new_state = not settings.get("is_running", False)
    if not db.toggle_poster(new_state):
        raise HTTPException(status_code=503, detail="Не вдалося зберегти стан розсилки")
    poster_worker.reset_errors()
    return {"is_running": new_state}


@web_app.post("/api/settings")
async def update_settings(req: SettingsUpdateRequest):
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    running = updates.pop("is_running", None)
    if updates and not db.update_settings(updates):
        raise HTTPException(status_code=503, detail="Не вдалося зберегти налаштування")
    if running is not None:
        if not db.toggle_poster(running):
            raise HTTPException(status_code=503, detail="Не вдалося зберегти стан розсилки")
        poster_worker.reset_errors()
    return {"status": "ok", "settings": db.get_settings()}


@web_app.get("/api/chats")
async def get_chats():
    return db.get_all_chats()


@web_app.post("/api/chats")
async def add_chat(req: ChatCreateRequest):
    post_id = req.post_id if req.post_id and req.post_id != "default" else None
    res = db.add_chat(req.chat_peer, req.interval_minutes, req.title or "", post_id=post_id)
    if not res:
        raise HTTPException(status_code=400, detail="Не вдалося додати чат")
    return res


@web_app.post("/api/chats/batch")
async def add_chats_batch(req: BatchChatsRequest):
    """Parse text and extract multiple chat usernames/links."""
    lines = req.text_links.strip().split("\n")
    added = 0
    post_id = req.post_id if req.post_id and req.post_id != "default" else None
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        match = re.search(r"(?:https?://t\.me/)?(@?[a-zA-Z0-9_+\-]+)", line)
        if match:
            peer = match.group(1)
            if not peer.startswith("+") and not peer.startswith("@") and not peer.startswith("joinchat"):
                peer = f"@{peer}"
            db.add_chat(peer, req.interval_minutes, post_id=post_id)
            added += 1
    return {"status": "ok", "added_count": added}


@web_app.put("/api/chats/{chat_id}")
async def update_chat(chat_id: str, req: ChatUpdateRequest):
    updates = {k: v for k, v in req.model_dump().items() if v is not None and k != "reset_status"}
    if req.reset_status:
        updates["status"] = "active"
        updates["last_error"] = None
        updates["is_active"] = True
    if "post_id" in updates and (updates["post_id"] == "default" or updates["post_id"] == ""):
        updates["post_id"] = None
    db.update_chat(chat_id, updates)
    return {"status": "ok"}


@web_app.delete("/api/chats/{chat_id}")
async def delete_chat(chat_id: str):
    success = db.delete_chat(chat_id)
    return {"status": "ok" if success else "error"}


@web_app.get("/api/posts")
async def get_posts():
    return {
        "active_post": db.get_active_post(),
        "all_posts": db.get_all_posts()
    }


@web_app.get("/api/posts/{post_id}")
async def get_single_post(post_id: str):
    post = db.get_post_by_id(post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Пост не знайдено")
    return post


@web_app.post("/api/posts")
async def save_post(req: PostSaveRequest):
    if req.post_id:
        updates = {
            "content": req.content,
            "title": req.title or "Оголошення"
        }
        if req.source_msg_id is not None:
            updates["source_msg_id"] = req.source_msg_id
            updates["source_chat_peer"] = req.source_chat_peer or "me"
        if req.is_active:
            updates["is_active"] = True
        db.update_post(req.post_id, updates)
        res = db.get_post_by_id(req.post_id)
    else:
        res = db.create_post(
            content=req.content,
            title=req.title or "Оголошення",
            source_msg_id=req.source_msg_id,
            source_chat_peer=req.source_chat_peer or "me",
            is_active=bool(req.is_active)
        )
    return {"status": "ok", "post": res}


@web_app.delete("/api/posts/{post_id}")
async def delete_post(post_id: str):
    all_posts = db.get_all_posts()
    if len(all_posts) <= 1:
        raise HTTPException(status_code=400, detail="Неможливо видалити останній варіант оголошення.")
    success = db.delete_post(post_id)
    return {"status": "ok" if success else "error"}


class GenerateTitleRequest(BaseModel):
    content: str


@web_app.post("/api/posts/generate-title")
async def generate_post_title(req: GenerateTitleRequest):
    title = await asyncio.to_thread(AIDiscoveryEngine.generate_title_for_post, req.content)
    return {"status": "ok", "title": title}


@web_app.post("/api/posts/{post_id}/set-default")
async def set_default_post(post_id: str):
    success = db.set_default_post(post_id)
    return {"status": "ok" if success else "error"}


@web_app.post("/api/posts/preview-spintax")
async def preview_spintax(req: SpintaxPreviewRequest):
    sample = SpintaxEngine.parse(req.text)
    return {"sample": sample}


@web_app.get("/api/telegram/saved-post")
async def fetch_from_saved_messages():
    """Import the latest message from Saved Messages with Telegram Premium Custom Emojis and message_id."""
    res = await get_latest_saved_message()
    return res


@web_app.post("/api/telegram/sync-saved-post")
async def sync_saved_post(req: Optional[SyncSavedPostRequest] = None):
    """Fetch latest message from 'Saved Messages' (me) and save it."""
    res = await get_latest_saved_message()
    if res.get("status") != "ok":
        return res

    msg_id = res.get("message_id")
    html_text = res.get("html_text") or res.get("raw_text") or ""
    
    post_id = req.post_id if req else None
    create_new = req.create_new if req else False

    if post_id and not create_new:
        db.update_post(post_id, {
            "content": html_text,
            "source_msg_id": msg_id,
            "source_chat_peer": "me"
        })
        saved_post = db.get_post_by_id(post_id)
    else:
        saved_post = db.create_post(
            content=html_text,
            title=f"Зі Збережених #{msg_id}",
            source_msg_id=msg_id,
            source_chat_peer="me",
            is_active=False
        )

    return {
        "status": "ok",
        "message": f"Оголошення #{msg_id} успішно завантажено!",
        "post": saved_post,
        "message_id": msg_id,
        "has_custom_emojis": res.get("has_custom_emojis"),
        "entities_count": res.get("entities_count")
    }


# ==================== CHAT FINDER & PARSER ====================

@web_app.post("/api/finder/search")
async def search_chats(req: SearchChatsRequest):
    """Search Telegram public groups by keywords."""
    raise HTTPException(status_code=410, detail="Використовуйте /api/discovery/search для безперервного пошуку.")


@web_app.post("/api/finder/add")
async def finder_add_chat(req: JoinAddChatRequest):
    """Join and add a found chat directly."""
    res = await ChatFinder.join_and_add_chat(
        peer_str=req.chat_peer,
        interval_minutes=req.interval_minutes,
        title=req.title or ""
    )
    return res


@web_app.post("/api/finder/batch-add")
async def finder_batch_add(req: BatchJoinAddRequest):
    """Batch join and add multiple chats."""
    added = 0
    for item in req.items:
        res = await ChatFinder.join_and_add_chat(
            peer_str=item.chat_peer,
            interval_minutes=item.interval_minutes,
            title=item.title or ""
        )
        if res.get("status") == "ok":
            added += 1
    return {"status": "ok", "added_count": added}


@web_app.get("/api/logs")
async def get_logs(limit: int = 50):
    return db.get_recent_logs(limit)


# ==================== TELEGRAM UI AUTHENTICATION ====================

@web_app.post("/api/auth/send-code")
async def send_auth_code(data: PhoneAuthRequest):
    """
    Step 1 of Telegram authentication: Request MTProto confirmation code to phone number.
    """
    phone = normalize_phone(data.phone)
    if not phone or len(phone) < 8:
        raise HTTPException(
            status_code=400,
            detail="Введіть коректний номер телефону (наприклад: +380991234567 або 0991234567)"
        )

    try:
        if not client.is_connected():
            await init_telegram_client()

        sent_code = await client.send_code_request(phone)
        _pending_auth[phone] = sent_code.phone_code_hash
        target_desc = describe_sent_code_type(sent_code.type)

        return {
            "status": "ok",
            "phone": phone,
            "phone_code_hash": sent_code.phone_code_hash,
            "delivery_type": type(sent_code.type).__name__ if sent_code.type else "App",
            "delivery_text": target_desc,
            "message": f"Код надіслано {target_desc}."
        }
    except PhoneNumberInvalidError:
        raise HTTPException(status_code=400, detail="Невірний номер телефону. Перевірте формат і спробуйте знову.")
    except PhoneNumberBannedError:
        raise HTTPException(status_code=400, detail="Цей номер телефону заблоковано в Telegram.")
    except FloodWaitError as e:
        raise HTTPException(status_code=429, detail=f"Забагато спроб. Telegram просить зачекати {e.seconds} сек перед повторною спробою.")
    except Exception as e:
        logger.error(f"Помилка надсилання коду для {phone}: {e}")
        err_msg = str(e)
        if "AuthKey" in err_msg or "session" in err_msg.lower():
            await reset_client_session()
            raise HTTPException(status_code=500, detail="Сесію відновлено після збою ключа. Будь ласка, спробуйте знову.")
        raise HTTPException(status_code=400, detail=f"Помилка надсилання коду: {err_msg}")


@web_app.post("/api/auth/verify-code")
async def verify_auth_code(data: VerifyCodeRequest):
    """
    Step 2 of Telegram authentication: Verify confirmation code received via Telegram or SMS.
    """
    phone = normalize_phone(data.phone)
    code = data.code.strip().replace(" ", "").replace("-", "")
    phone_code_hash = data.phone_code_hash or _pending_auth.get(phone)

    if not code:
        raise HTTPException(status_code=400, detail="Введіть код підтвердження")

    try:
        await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
    except SessionPasswordNeededError:
        return {
            "status": "needs_2fa",
            "message": "Для вашого акаунта активовано двоетапну перевірку (2FA). Будь ласка, введіть хмарний пароль."
        }
    except PhoneCodeInvalidError:
        raise HTTPException(status_code=400, detail="Невірний код підтвердження! Перевірте цифри та спробуйте ще раз.")
    except PhoneCodeExpiredError:
        raise HTTPException(status_code=400, detail="Термін дії коду закінчився. Запитайте новий код.")
    except FloodWaitError as e:
        raise HTTPException(status_code=429, detail=f"Забагато спроб. Зачекайте {e.seconds} секунд.")
    except Exception as e:
        logger.error(f"Помилка підтвердження коду: {e}")
        raise HTTPException(status_code=400, detail=f"Помилка авторизації: {str(e)}")

    _pending_auth.pop(phone, None)
    me = await client.get_me()
    return {
        "status": "ok",
        "message": "Авторизація успішна!",
        "user": {
            "id": me.id,
            "name": f"{me.first_name} {me.last_name or ''}".strip(),
            "username": me.username,
            "phone": me.phone,
            "is_premium": getattr(me, "premium", False)
        }
    }


@web_app.post("/api/auth/verify-2fa")
async def verify_auth_2fa(data: Verify2FARequest):
    """
    Step 3 of Telegram authentication (optional): Verify 2FA Cloud Password if enabled.
    """
    password = data.password.strip()
    if not password:
        raise HTTPException(status_code=400, detail="Введіть 2FA пароль")

    try:
        await client.sign_in(password=password)
    except PasswordHashInvalidError:
        raise HTTPException(status_code=400, detail="Невірний пароль 2FA! Перевірте пароль і спробуйте знову.")
    except FloodWaitError as e:
        raise HTTPException(status_code=429, detail=f"Забагато спроб. Зачекайте {e.seconds} секунд.")
    except Exception as e:
        logger.error(f"Помилка 2FA авторизації: {e}")
        raise HTTPException(status_code=400, detail=f"Помилка 2FA авторизації: {str(e)}")

    _pending_auth.clear()
    me = await client.get_me()
    return {
        "status": "ok",
        "message": "Авторизація успішна!",
        "user": {
            "id": me.id,
            "name": f"{me.first_name} {me.last_name or ''}".strip(),
            "username": me.username,
            "phone": me.phone,
            "is_premium": getattr(me, "premium", False)
        }
    }


@web_app.post("/api/auth/logout")
async def handle_logout():
    """Log out of Telegram account and reset session."""
    success = await logout_client()
    if success:
        try:
            from bot.admin_bot import notify_admins
            await notify_admins(
                "🚪 <b>Сесію Telegram Userbot завершено!</b>\n\n"
                "Користувач вийшов з акаунту через веб-панель. Для розсилки потрібна нова авторизація."
            )
        except Exception:
            pass
    return {
        "status": "ok" if success else "error",
        "message": "Ви успішно вийшли з Telegram акаунту" if success else "Помилка виходу з акаунту"
    }


@web_app.post("/api/auth/reset-session")
async def handle_reset_session():
    """Reset session file and reconnect fresh client."""
    success = await reset_client_session()
    return {
        "status": "ok" if success else "error",
        "message": "Сесію успішно скинуто, готово до повторної авторизації" if success else "Не вдалося скинути файл сесії"
    }


@web_app.post("/api/auth/login-bot-token")
async def handle_login_bot_token(req: Optional[BotTokenAuthRequest] = None):
    """
    Log in immediately using a Telegram Bot Token (from BotFather).
    Does NOT require a phone number or SMS/Telegram verification code.
    """
    token = req.bot_token if req and req.bot_token else None
    res = await login_with_bot_token(token)
    if res.get("status") != "ok":
        raise HTTPException(status_code=400, detail=res.get("message", "Помилка авторизації бота"))
    return res
