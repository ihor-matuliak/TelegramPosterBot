import os
import asyncio
import logging
from telethon import TelegramClient
from telethon.errors import (
    FloodWaitError,
    SlowModeWaitError,
    AuthKeyDuplicatedError,
    AuthKeyUnregisteredError,
    SessionRevokedError,
    SessionPasswordNeededError,
    PasswordHashInvalidError
)
from telethon.tl.types import (
    MessageEntityCustomEmoji,
    SendMessageTypingAction
)
from telethon.tl.functions.messages import SetTypingRequest
from telethon.extensions import html
from telethon.sessions import StringSession
from config import config, BASE_DIR

logger = logging.getLogger("TelegramClient")


def _create_raw_client(loop=None) -> TelegramClient:
    string_session = os.getenv("TELEGRAM_STRING_SESSION", "").strip().strip('"').strip("'")
    session_name = (config.TELEGRAM_SESSION_NAME or "poster_session").strip().strip('"').strip("'")

    # If TELEGRAM_SESSION_NAME was mistakenly filled with a StringSession
    if not string_session and len(session_name) > 60 and session_name.startswith("1"):
        string_session = session_name

    if string_session:
        session_target = StringSession(string_session)
    else:
        safe_name = "poster_session" if len(session_name) > 50 else session_name
        session_target = str(BASE_DIR / safe_name)

    safe_api_id = config.TELEGRAM_API_ID if config.TELEGRAM_API_ID else 1
    safe_api_hash = config.TELEGRAM_API_HASH if config.TELEGRAM_API_HASH else "00000000000000000000000000000000"

    kwargs = {
        "api_id": safe_api_id,
        "api_hash": safe_api_hash,
        "device_model": "Desktop",
        "system_version": "Windows 10",
        "app_version": "4.16.8 x64",
        "lang_code": "en",
        "system_lang_code": "en",
        "flood_sleep_threshold": 0
    }
    if loop is not None:
        kwargs["loop"] = loop

    return TelegramClient(session_target, **kwargs)


def normalize_phone(raw: str) -> str:
    """Normalize phone number to international E.164 format (+380...)."""
    clean = "".join(ch for ch in str(raw).strip() if ch.isdigit() or ch == "+")
    if clean.startswith("+"):
        digits = clean[1:]
    else:
        digits = clean

    if not digits:
        return ""

    # Ukrainian formats:
    # 0991234567 (10 digits starting with 0) -> +380991234567
    if len(digits) == 10 and digits.startswith("0"):
        return f"+38{digits}"
    # 380991234567 (12 digits starting with 380) -> +380991234567
    if len(digits) == 12 and digits.startswith("380"):
        return f"+{digits}"
    # 80991234567 (11 digits starting with 80) -> +3{digits}
    if len(digits) == 11 and digits.startswith("80"):
        return f"+3{digits}"

    return f"+{digits}"


def describe_sent_code_type(sent_code_type) -> str:
    """Human-friendly Ukrainian description of where Telegram sent the confirmation code."""
    type_name = type(sent_code_type).__name__ if sent_code_type else ""
    if "App" in type_name:
        return "у додаток Telegram (офіційний системний чат «Telegram» з синьою галочкою від сервісу 777000)"
    elif "Sms" in type_name or "Fragment" in type_name or "Firebase" in type_name:
        return "у звичайне SMS-повідомлення на ваш телефонний номер"
    elif "Call" in type_name or "FlashCall" in type_name or "MissedCall" in type_name:
        return "через вхідний дзвінок від Telegram"
    elif "Email" in type_name:
        return "на електронну пошту, прив'язану до вашого акаунту Telegram"
    elif "SetUpEmail" in type_name:
        return "Telegram вимагає спочатку прив'язати email в офіційному додатку (Налаштування -> Конфіденційність -> Вхід за допомогою ел. пошти)"
    return "у додаток Telegram (офіційний системний чат «Telegram» з синьою галочкою)"


class TelegramClientProxy:
    """
    Transparent proxy around TelegramClient that allows swapping/resetting
    the underlying MTProto client without breaking imported references across modules.
    """
    def __init__(self, target: TelegramClient):
        self.__dict__["_target"] = target

    def set_target(self, new_target: TelegramClient):
        self.__dict__["_target"] = new_target

    def get_target(self) -> TelegramClient:
        return self.__dict__["_target"]

    def __getattr__(self, name: str):
        return getattr(self._target, name)

    def __setattr__(self, name: str, value):
        if name == "_target":
            self.__dict__[name] = value
        else:
            setattr(self._target, name, value)

    def __call__(self, *args, **kwargs):
        return self._target(*args, **kwargs)


client = TelegramClientProxy(_create_raw_client())


def cleanup_session_files(session_name: str | None = None):
    """Completely remove all SQLite session files from disk so fresh sessions can start cleanly."""
    name = session_name or config.TELEGRAM_SESSION_NAME
    patterns = [
        f"{name}.session",
        f"{name}.session-journal",
        f"{name}.session.bak",
        f"{name}.session.bak-journal",
        "poster_session.session",
        "poster_session.session-journal",
        "poster_session.session.bak",
        "poster_session.session.bak-journal",
        "temp.session",
        "temp.session-journal"
    ]
    for filename in patterns:
        path = BASE_DIR / filename
        if path.exists():
            try:
                path.unlink()
                logger.info(f"Видалено файл сесії: {filename}")
            except Exception as e:
                logger.warning(f"Не вдалося видалити {filename}: {e}")

    try:
        for p in BASE_DIR.glob("*.session*"):
            try:
                if p.is_file():
                    p.unlink()
                    logger.info(f"Видалено залишковий файл сесії: {p.name}")
            except Exception as e:
                logger.warning(f"Не вдалося видалити {p.name}: {e}")
    except Exception:
        pass


async def reset_client_session(delete_files: bool = True) -> bool:
    """
    Safely disconnects client, closes SQLite session handle, wipes session files from disk,
    creates a fresh client instance and connects it so it is ready for login on any server.
    """
    logger.info("Скидання та очищення Telegram сесії...")
    try:
        target = client.get_target() if hasattr(client, "get_target") else None
        if target:
            if hasattr(target, "session") and target.session:
                try:
                    if hasattr(target.session, "delete"):
                        target.session.delete()
                    if hasattr(target.session, "close"):
                        target.session.close()
                except Exception:
                    pass
        if client.is_connected():
            await client.disconnect()
    except Exception as e:
        logger.warning(f"Помилка відключення перед скиданням: {e}")

    if delete_files:
        cleanup_session_files()
    else:
        session_file = BASE_DIR / f"{config.TELEGRAM_SESSION_NAME}.session"
        if session_file.exists():
            backup_file = BASE_DIR / f"{config.TELEGRAM_SESSION_NAME}.session.bak"
            try:
                if backup_file.exists():
                    backup_file.unlink()
                session_file.rename(backup_file)
                logger.info(f"Старий файл сесії переміщено в {backup_file.name}")
            except Exception as e:
                logger.warning(f"Не вдалося перейменувати файл сесії, спроба видалення: {e}")
                try:
                    session_file.unlink()
                except Exception:
                    pass

        journal_file = BASE_DIR / f"{config.TELEGRAM_SESSION_NAME}.session-journal"
        if journal_file.exists():
            try:
                journal_file.unlink()
            except Exception:
                pass

    cur_loop = None
    try:
        cur_loop = asyncio.get_running_loop()
    except Exception:
        pass
    new_raw = _create_raw_client(loop=cur_loop)
    client.set_target(new_raw)
    try:
        if "qr_auth" in globals():
            qr_auth.reset()
    except Exception:
        pass
    try:
        await client.connect()
        logger.info("Новий клієнт Telegram успішно підключено та готовий до авторизації.")
        return True
    except Exception as e:
        logger.error(f"Помилка підключення нового клієнта: {e}")
        return False


async def logout_client() -> bool:
    """Log out from current Telegram session, terminate it on Telegram servers, and completely wipe local session files."""
    try:
        if client.is_connected() and await client.is_user_authorized():
            await client.log_out()
            logger.info("Сесію успішно завершено на серверах Telegram (log_out).")
    except Exception as e:
        logger.warning(f"Помилка при виході з акаунту (log_out): {e}")

    return await reset_client_session(delete_files=True)


async def login_with_bot_token(bot_token: str | None = None, retry_on_auth_err: bool = True) -> dict:
    """
    Log in using a Telegram Bot Token (from BotFather).
    Does NOT require a phone number, SMS, or Telegram confirmation code.
    """
    token = (bot_token or config.BOT_TOKEN or "").strip()
    if not token or ":" not in token:
        return {"status": "error", "message": "Некоректний або порожній Bot Token"}

    try:
        if not client.is_connected():
            try:
                await client.connect()
            except Exception as conn_err:
                logger.warning(f"Не вдалося підключити клієнт ({conn_err}), скидаємо сесію...")
                await reset_client_session(delete_files=True)

        await client.sign_in(bot_token=token)
        me = await client.get_me()
        logger.info(f"Успішна авторизація через Bot Token: {me.first_name} (@{me.username}) [ID: {me.id}]")

        # Persist custom token if provided
        if bot_token and bot_token.strip():
            config.BOT_TOKEN = token
            env_path = BASE_DIR / ".env"
            if env_path.exists():
                try:
                    content = env_path.read_text(encoding="utf-8")
                    if "BOT_TOKEN=" in content:
                        lines = content.splitlines()
                        new_lines = [
                            f"BOT_TOKEN={token}" if line.startswith("BOT_TOKEN=") else line
                            for line in lines
                        ]
                        env_path.write_text("\n".join(new_lines), encoding="utf-8")
                except Exception as env_err:
                    logger.warning(f"Не вдалося оновити BOT_TOKEN у .env: {env_err}")

        return {
            "status": "ok",
            "message": f"Успішно авторизовано через Bot Token (@{me.username or me.id})!",
            "user": {
                "id": me.id,
                "name": f"{me.first_name} {me.last_name or ''}".strip(),
                "username": me.username,
                "phone": None,
                "is_bot": True,
                "is_premium": False
            }
        }
    except (AuthKeyDuplicatedError, AuthKeyUnregisteredError, SessionRevokedError) as e:
        logger.warning(f"Конфлікт/помилка сесії ({type(e).__name__}). Повне видалення сесії та повторна спроба...")
        if retry_on_auth_err:
            await reset_client_session(delete_files=True)
            return await login_with_bot_token(bot_token=bot_token, retry_on_auth_err=False)
        return {"status": "error", "message": f"Помилка авторизації токена: {str(e)}"}
    except Exception as e:
        err_msg = str(e)
        if retry_on_auth_err and ("AuthKey" in err_msg or "session" in err_msg.lower() or "different IP" in err_msg):
            logger.warning(f"Виявлено недійсну сесію ({err_msg}). Повне видалення сесії та повторна спроба...")
            await reset_client_session(delete_files=True)
            return await login_with_bot_token(bot_token=bot_token, retry_on_auth_err=False)
        logger.error(f"Помилка авторизації через Bot Token: {e}")
        return {"status": "error", "message": f"Помилка авторизації токена: {str(e)}"}


async def init_telegram_client() -> TelegramClient:
    """Initialize and connect the Telethon MTProto client with auto-recovery."""
    if not config.TELEGRAM_API_ID or not config.TELEGRAM_API_HASH:
        logger.error(
            "❌ [КРИТИЧНО] Змінні TELEGRAM_API_ID або TELEGRAM_API_HASH не налаштовані! "
            "Будь ласка, перевірте файл .env"
        )
        return client

    try:
        cur_loop = asyncio.get_running_loop()
        target = client.get_target() if hasattr(client, "get_target") else None
        if target and hasattr(target, "loop") and target.loop != cur_loop:
            logger.info("Event loop changed, recreating TelegramClient on current running loop...")
            new_target = _create_raw_client(loop=cur_loop)
            client.set_target(new_target)
    except Exception:
        pass

    try:
        if not client.is_connected():
            logger.info("Connecting to Telegram MTProto...")
            await client.connect()
    except (AuthKeyDuplicatedError, AuthKeyUnregisteredError, SessionRevokedError) as e:
        logger.warning(f"Сесійний ключ недійсний або дубльований ({type(e).__name__}). Автоматичне відновлення...")
        await reset_client_session()
    except Exception as e:
        err_msg = str(e)
        if "AuthKey" in err_msg or "different IP addresses" in err_msg or "session" in err_msg.lower():
            logger.warning(f"Сесійний ключ недійсний ({err_msg}). Автоматичне відновлення...")
            await reset_client_session()
        else:
            logger.error(f"Помилка з'єднання з Telegram MTProto: {e}")

    try:
        if not await client.is_user_authorized():
            logger.warning("Telegram client is NOT authorized yet! Please log in via Web UI or python auth.py.")
        else:
            me = await client.get_me()
            logger.info(f"Telegram client connected as: {me.first_name} (@{me.username or 'no_username'}) [ID: {me.id}]")
    except (AuthKeyDuplicatedError, AuthKeyUnregisteredError, SessionRevokedError) as e:
        logger.warning(f"Сесія анульована при перевірці ({type(e).__name__}). Скидання сесії...")
        await reset_client_session()
    except Exception as e:
        err_msg = str(e)
        if "AuthKey" in err_msg or "different IP addresses" in err_msg or "session" in err_msg.lower():
            logger.warning(f"Сесія анульована ({err_msg}). Скидання сесії...")
            await reset_client_session()
        else:
            logger.warning(f"Помилка перевірки авторизації: {e}")

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
    except (FloodWaitError, SlowModeWaitError):
        raise
    except Exception:
        pass


class QRAuthManager:
    """Manages Telegram QR Code authentication workflow for logging in as a user profile."""
    def __init__(self):
        self._qr_login = None
        self._task: asyncio.Task | None = None
        self._status: str = "idle"  # idle, waiting, needs_2fa, success, error, expired
        self._user_info: dict | None = None
        self._error_msg: str | None = None
        self._url: str | None = None
        self._svg: str | None = None
        self._expires: str | None = None

    def get_state(self) -> dict:
        return {
            "status": self._status,
            "url": self._url,
            "svg": self._svg,
            "expires": self._expires,
            "user": self._user_info,
            "error": self._error_msg
        }

    async def start(self) -> dict:
        """Start or refresh a Telegram QR code login session."""
        self._cancel_task()
        self._status = "waiting"
        self._user_info = None
        self._error_msg = None

        if not client.is_connected():
            await init_telegram_client()

        try:
            self._qr_login = await client.qr_login()
            self._url = self._qr_login.url
            self._expires = self._qr_login.expires.isoformat() if getattr(self._qr_login, "expires", None) else None
            self._svg = self._render_svg(self._url)
            self._task = asyncio.create_task(self._wait_loop())
            return self.get_state()
        except Exception as e:
            logger.error(f"Error starting QR login: {e}")
            self._status = "error"
            self._error_msg = str(e)
            return self.get_state()

    def _render_svg(self, url: str) -> str:
        try:
            import qrcode
            import qrcode.image.svg
            import io
            qr = qrcode.QRCode(
                version=1,
                error_correction=qrcode.constants.ERROR_CORRECT_M,
                box_size=8,
                border=2,
                image_factory=qrcode.image.svg.SvgPathImage
            )
            qr.add_data(url)
            qr.make(fit=True)
            img = qr.make_image()
            buf = io.BytesIO()
            img.save(buf)
            return buf.getvalue().decode("utf-8")
        except Exception as e:
            logger.warning(f"Could not render SVG QR code: {e}")
            return ""

    async def _wait_loop(self):
        try:
            await self._qr_login.wait(timeout=120)
            me = await client.get_me()
            self._status = "success"
            self._user_info = {
                "id": me.id,
                "name": f"{me.first_name} {me.last_name or ''}".strip(),
                "username": me.username,
                "phone": me.phone,
                "is_bot": getattr(me, "bot", False),
                "is_premium": getattr(me, "premium", False)
            }
            logger.info(f"QR Login successful as user: {self._user_info['name']} (@{self._user_info['username']})")
        except SessionPasswordNeededError:
            self._status = "needs_2fa"
            self._error_msg = "Для вашого акаунта активовано двоетапну перевірку (2FA). Введіть хмарний пароль."
            logger.info("QR Login requires 2FA cloud password.")
        except (asyncio.TimeoutError, Exception) as e:
            if isinstance(e, asyncio.CancelledError):
                return
            err_name = type(e).__name__
            if "Timeout" in err_name or "expired" in str(e).lower():
                self._status = "expired"
                self._error_msg = "Термін дії QR-коду вичерпано. Натисніть «Оновити QR-код»."
            else:
                self._status = "error"
                self._error_msg = str(e)
                logger.error(f"Error during QR login wait: {e}")

    async def submit_2fa(self, password: str) -> dict:
        if self._status != "needs_2fa":
            return {"status": "error", "message": "2FA не очікується"}
        try:
            await client.sign_in(password=password.strip())
            me = await client.get_me()
            self._status = "success"
            self._user_info = {
                "id": me.id,
                "name": f"{me.first_name} {me.last_name or ''}".strip(),
                "username": me.username,
                "phone": me.phone,
                "is_bot": getattr(me, "bot", False),
                "is_premium": getattr(me, "premium", False)
            }
            return {"status": "ok", "user": self._user_info}
        except PasswordHashInvalidError:
            return {"status": "error", "message": "Невірний хмарний пароль 2FA! Перевірте пароль і спробуйте знову."}
        except Exception as e:
            return {"status": "error", "message": f"Помилка 2FA авторизації: {e}"}

    def _cancel_task(self):
        if self._task and not self._task.done():
            self._task.cancel()
            self._task = None

    def reset(self):
        self._cancel_task()
        self._qr_login = None
        self._status = "idle"
        self._user_info = None
        self._error_msg = None
        self._url = None
        self._svg = None
        self._expires = None


qr_auth = QRAuthManager()

