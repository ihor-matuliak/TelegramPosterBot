"""Additional Telegram posting accounts: login flows and one poster worker per account.

The primary account keeps living in core.client (env session / session file).
Additional accounts are stored as Telethon StringSessions in the Supabase
"accounts" table, so they survive redeploys on hosts with ephemeral disks.
"""

import asyncio
import hashlib
import io
import logging
import time
import uuid
from typing import Any

from telethon import TelegramClient
from telethon.errors import (
    FloodWaitError,
    PasswordHashInvalidError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneNumberBannedError,
    PhoneNumberInvalidError,
    SessionPasswordNeededError,
)
from telethon.sessions import StringSession

from config import config
from core.poster import PosterWorker
from database.client import db

logger = logging.getLogger("Accounts")

LOGIN_TTL_SECONDS = 15 * 60
SYNC_INTERVAL_SECONDS = 15
START_RETRY_SECONDS = 300


class AccountLoginError(Exception):
    """A user-facing login failure (message is shown as is)."""


def create_account_client(session_string: str = "") -> TelegramClient:
    """A Telethon client with the same device fingerprint as the primary account."""
    return TelegramClient(
        StringSession(session_string or None),
        api_id=config.TELEGRAM_API_ID or 1,
        api_hash=config.TELEGRAM_API_HASH or "00000000000000000000000000000000",
        device_model="Desktop",
        system_version="Windows 10",
        app_version="4.16.8 x64",
        lang_code="en",
        system_lang_code="en",
        flood_sleep_threshold=0,
    )


def describe_user(me: Any) -> dict[str, Any]:
    return {
        "id": me.id,
        "name": f"{me.first_name or ''} {me.last_name or ''}".strip(),
        "username": me.username,
        "phone": me.phone,
        "is_bot": getattr(me, "bot", False),
        "is_premium": getattr(me, "premium", False),
    }


def render_qr_svg(url: str) -> str:
    try:
        import qrcode
        import qrcode.image.svg
        qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=8, border=2,
                           image_factory=qrcode.image.svg.SvgPathImage)
        qr.add_data(url)
        qr.make(fit=True)
        buf = io.BytesIO()
        qr.make_image().save(buf)
        return buf.getvalue().decode("utf-8")
    except Exception as e:
        logger.warning(f"Could not render SVG QR code: {e}")
        return ""


async def _primary_user_id() -> int | None:
    try:
        from core.client import client as primary
        if primary.is_connected() and await primary.is_user_authorized():
            return (await primary.get_me()).id
    except Exception:
        pass
    return None


class AccountLogin:
    """One in-progress login of a new account (phone code or QR), with its own temporary client."""

    def __init__(self, method: str):
        self.id = uuid.uuid4().hex
        self.method = method
        self.client = create_account_client()
        self.status = "starting"  # code_sent, waiting_qr, needs_2fa, success, error, expired
        self.created = time.monotonic()
        self.phone: str | None = None
        self.phone_code_hash: str | None = None
        self.sent_code_type: Any = None
        self.delivery_type: str | None = None
        self.qr = None
        self.qr_url: str | None = None
        self.qr_svg: str | None = None
        self.qr_expires: str | None = None
        self.task: asyncio.Task | None = None
        self.account: dict[str, Any] | None = None
        self.user: dict[str, Any] | None = None
        self.error: str | None = None
        self.lock = asyncio.Lock()

    def state(self) -> dict[str, Any]:
        return {
            "login_id": self.id,
            "method": self.method,
            "status": self.status,
            "phone": self.phone,
            "delivery_type": self.delivery_type,
            "url": self.qr_url,
            "svg": self.qr_svg,
            "expires": self.qr_expires,
            "account": self.account,
            "user": self.user,
            "error": self.error,
        }

    async def close(self) -> None:
        if self.task and not self.task.done():
            self.task.cancel()
        try:
            await self.client.disconnect()
        except Exception:
            pass


class AccountLoginManager:
    def __init__(self) -> None:
        self._logins: dict[str, AccountLogin] = {}

    async def _cleanup(self) -> None:
        now = time.monotonic()
        for login_id, login in list(self._logins.items()):
            if now - login.created > LOGIN_TTL_SECONDS:
                self._logins.pop(login_id, None)
                await login.close()

    def _ready(self) -> None:
        if not config.TELEGRAM_API_ID or not config.TELEGRAM_API_HASH:
            raise AccountLoginError("TELEGRAM_API_ID / TELEGRAM_API_HASH не налаштовані на сервері.")
        if not db.supports_accounts():
            raise AccountLoginError(
                "Таблиця акаунтів ще не створена. Виконайте міграцію "
                "database/migrations/20261015_multi_accounts.sql у Supabase."
            )

    def get(self, login_id: str) -> AccountLogin:
        login = self._logins.get(login_id)
        if not login:
            raise AccountLoginError("Сесію входу не знайдено або вона застаріла. Почніть додавання акаунта знову.")
        return login

    async def start_phone(self, phone: str) -> AccountLogin:
        await self._cleanup()
        self._ready()
        login = AccountLogin("phone")
        try:
            await login.client.connect()
            sent = await login.client.send_code_request(phone)
        except PhoneNumberInvalidError:
            await login.close()
            raise AccountLoginError("Невірний номер телефону. Перевірте формат і спробуйте знову.")
        except PhoneNumberBannedError:
            await login.close()
            raise AccountLoginError("Цей номер телефону заблоковано в Telegram.")
        except FloodWaitError as e:
            await login.close()
            raise AccountLoginError(f"Забагато спроб. Telegram просить зачекати {e.seconds} сек.")
        except Exception as e:
            await login.close()
            logger.error(f"Error sending login code for new account: {e}")
            raise AccountLoginError(f"Помилка надсилання коду: {e}")
        login.phone = phone
        login.phone_code_hash = sent.phone_code_hash
        login.sent_code_type = sent.type
        login.delivery_type = type(sent.type).__name__ if sent.type else "App"
        login.status = "code_sent"
        self._logins[login.id] = login
        return login

    async def submit_code(self, login_id: str, code: str) -> AccountLogin:
        login = self.get(login_id)
        code = "".join(ch for ch in code if ch.isdigit())
        if not code:
            raise AccountLoginError("Введіть код підтвердження.")
        async with login.lock:
            try:
                await login.client.sign_in(phone=login.phone, code=code, phone_code_hash=login.phone_code_hash)
            except SessionPasswordNeededError:
                login.status = "needs_2fa"
                return login
            except PhoneCodeInvalidError:
                raise AccountLoginError("Невірний код підтвердження! Перевірте цифри та спробуйте ще раз.")
            except PhoneCodeExpiredError:
                raise AccountLoginError(
                    "Термін дії коду закінчився (або Telegram анулював код, бо його переслали в повідомленні). "
                    "Запитайте новий код."
                )
            except FloodWaitError as e:
                raise AccountLoginError(f"Забагато спроб. Зачекайте {e.seconds} секунд.")
            except Exception as e:
                raise AccountLoginError(f"Помилка авторизації: {e}")
            await self._finish(login)
        return login

    async def submit_password(self, login_id: str, password: str) -> AccountLogin:
        login = self.get(login_id)
        if login.status != "needs_2fa":
            raise AccountLoginError("Хмарний пароль 2FA зараз не очікується.")
        password = password.strip()
        if not password:
            raise AccountLoginError("Введіть хмарний пароль 2FA.")
        async with login.lock:
            try:
                await login.client.sign_in(password=password)
            except PasswordHashInvalidError:
                raise AccountLoginError("Невірний пароль 2FA! Перевірте пароль і спробуйте знову.")
            except FloodWaitError as e:
                raise AccountLoginError(f"Забагато спроб. Зачекайте {e.seconds} секунд.")
            except Exception as e:
                raise AccountLoginError(f"Помилка 2FA авторизації: {e}")
            await self._finish(login)
        return login

    async def start_qr(self) -> AccountLogin:
        await self._cleanup()
        self._ready()
        login = AccountLogin("qr")
        try:
            await login.client.connect()
            login.qr = await login.client.qr_login()
        except Exception as e:
            await login.close()
            logger.error(f"Error starting QR login for new account: {e}")
            raise AccountLoginError(f"Не вдалося створити QR-код: {e}")
        self._set_qr(login)
        login.status = "waiting_qr"
        login.task = asyncio.create_task(self._wait_qr(login))
        self._logins[login.id] = login
        return login

    async def refresh_qr(self, login_id: str) -> AccountLogin:
        login = self.get(login_id)
        if login.method != "qr" or login.status in ("success", "needs_2fa"):
            return login
        if login.task and not login.task.done():
            login.task.cancel()
        try:
            await login.qr.recreate()
        except Exception as e:
            raise AccountLoginError(f"Не вдалося оновити QR-код: {e}")
        self._set_qr(login)
        login.status, login.error = "waiting_qr", None
        login.task = asyncio.create_task(self._wait_qr(login))
        return login

    @staticmethod
    def _set_qr(login: AccountLogin) -> None:
        login.qr_url = login.qr.url
        expires = getattr(login.qr, "expires", None)
        login.qr_expires = expires.isoformat() if expires else None
        login.qr_svg = render_qr_svg(login.qr_url)

    async def _wait_qr(self, login: AccountLogin) -> None:
        try:
            await login.qr.wait(timeout=120)
            async with login.lock:
                await self._finish(login)
        except asyncio.CancelledError:
            return
        except SessionPasswordNeededError:
            login.status = "needs_2fa"
        except asyncio.TimeoutError:
            login.status, login.error = "expired", "Термін дії QR-коду вичерпано. Натисніть «Оновити QR-код»."
        except AccountLoginError as e:
            login.status, login.error = "error", str(e)
        except Exception as e:
            logger.error(f"Error during QR login of new account: {e}")
            login.status, login.error = "error", str(e)

    async def _finish(self, login: AccountLogin) -> None:
        """Persist the authorized session and release the temporary client."""
        me = await login.client.get_me()
        user = describe_user(me)
        if user["is_bot"]:
            await login.client.log_out()
            raise AccountLoginError("Це бот. Для розсилки потрібен звичайний профіль Telegram.")
        if user["id"] == await _primary_user_id():
            # Terminates only the redundant session just created here, never the primary one.
            await login.client.log_out()
            raise AccountLoginError("Цей Telegram-акаунт уже підключено як основний.")
        session_string = StringSession.save(login.client.session)
        account = db.save_account_session(user, session_string)
        if not account:
            raise AccountLoginError("Авторизація вдалася, але не вдалося зберегти акаунт у базі даних.")
        login.status, login.user, login.account, login.error = "success", user, account, None
        logger.info(f"New posting account added: {user['name']} (@{user['username']}) [ID: {user['id']}]")
        db.add_log("SYSTEM", "account_added", f"Додано акаунт для розсилки: {account.get('label')}")
        # The supervisor opens its own client for posting; this one must not keep the same auth key busy.
        try:
            await login.client.disconnect()
        except Exception:
            pass

    async def cancel(self, login_id: str) -> None:
        login = self._logins.pop(login_id, None)
        if login:
            await login.close()

    async def close(self) -> None:
        for login_id in list(self._logins):
            await self.cancel(login_id)


def _session_fingerprint(session_string: str) -> str:
    return hashlib.sha256(session_string.encode()).hexdigest()


class AccountPosterSupervisor:
    """Keeps exactly one PosterWorker running per additional account with posting enabled."""

    def __init__(self) -> None:
        self.workers: dict[str, PosterWorker] = {}
        self.clients: dict[str, TelegramClient] = {}
        self._fingerprints: dict[str, str] = {}
        self._retry_after: dict[str, float] = {}
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        if not config.ACCOUNT_WORKERS_ENABLED:
            logger.warning("ACCOUNT_WORKERS_ENABLED=false: additional accounts will not post from this process.")
            return
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="account-posters")

    async def _run(self) -> None:
        while True:
            try:
                await self.sync()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Account supervisor error: {e}", exc_info=True)
            await asyncio.sleep(SYNC_INTERVAL_SECONDS)

    async def sync(self) -> None:
        """Start workers for enabled accounts, stop them for disabled, deleted or re-logged accounts."""
        async with self._lock:
            if not config.ACCOUNT_WORKERS_ENABLED or not db.supports_accounts():
                return
            accounts = db.get_accounts(include_session=True)
            wanted = {a["id"]: a for a in accounts if a.get("posting_enabled") and a.get("session_string")}
            for account_id in list(self.workers):
                account = wanted.get(account_id)
                if not account or _session_fingerprint(account["session_string"]) != self._fingerprints.get(account_id):
                    await self._stop_worker(account_id)
            for account_id, account in wanted.items():
                if account_id not in self.workers and time.monotonic() >= self._retry_after.get(account_id, 0):
                    await self._start_worker(account)

    async def _start_worker(self, account: dict[str, Any]) -> None:
        account_id = account["id"]
        label = account.get("label") or account_id[:8]
        tg = create_account_client(account["session_string"])
        try:
            await tg.connect()
            authorized = await tg.is_user_authorized()
        except Exception as e:
            logger.warning(f"Could not connect account {label}: {e}")
            self._retry_after[account_id] = time.monotonic() + START_RETRY_SECONDS
            db.update_account(account_id, {"status": "error", "last_error": f"Не вдалося підключитися: {e}"})
            await self._disconnect(tg)
            return
        if not authorized:
            reason = "Сесію акаунта завершено в Telegram. Додайте акаунт повторно."
            db.update_account(account_id, {"posting_enabled": False, "status": "unauthorized", "last_error": reason})
            db.add_log("SYSTEM", "account_paused", f"{label}: {reason}", account=account_id)
            await self._disconnect(tg)
            return
        if account.get("status") != "active":
            db.update_account(account_id, {"status": "active", "last_error": None})
        worker = PosterWorker(account=account_id, telegram=tg, label=label)
        worker.authorized = True
        self.workers[account_id] = worker
        self.clients[account_id] = tg
        self._fingerprints[account_id] = _session_fingerprint(account["session_string"])
        self._retry_after.pop(account_id, None)
        await worker.start()
        logger.info(f"Posting worker started for account {label}.")

    async def _stop_worker(self, account_id: str) -> None:
        worker = self.workers.pop(account_id, None)
        tg = self.clients.pop(account_id, None)
        self._fingerprints.pop(account_id, None)
        if worker:
            await worker.stop()
        if tg:
            await self._disconnect(tg)

    @staticmethod
    async def _disconnect(tg: TelegramClient) -> None:
        try:
            await tg.disconnect()
        except Exception:
            pass

    async def remove_account(self, account_id: str, logout: bool = True) -> bool:
        """Stop posting, optionally terminate the session in Telegram, and delete the account with its chats."""
        async with self._lock:
            await self._stop_worker(account_id)
            if logout:
                account = db.get_account(account_id, include_session=True)
                if account and account.get("session_string"):
                    tg = create_account_client(account["session_string"])
                    try:
                        await tg.connect()
                        if await tg.is_user_authorized():
                            await tg.log_out()
                    except Exception as e:
                        logger.warning(f"Could not log out account {account_id}: {e}")
                    finally:
                        await self._disconnect(tg)
            self._retry_after.pop(account_id, None)
            return db.delete_account(account_id)

    def worker_status(self, account_id: str, settings: dict[str, Any], hourly: int, daily: int) -> dict[str, Any]:
        worker = self.workers.get(account_id)
        if not config.ACCOUNT_WORKERS_ENABLED:
            return {"state": "stopped", "reason": "Воркери акаунтів вимкнені на цьому сервері", "until": None}
        if not worker:
            return {"state": "disabled", "reason": "Розсилку з акаунта вимкнено", "until": None}
        return worker.get_status(settings, bool(worker.authorized), hourly, daily)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        for account_id in list(self.workers):
            await self._stop_worker(account_id)


account_logins = AccountLoginManager()
account_posters = AccountPosterSupervisor()
