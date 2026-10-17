import asyncio
import random
import logging
from datetime import datetime, timezone, timedelta
from enum import Enum
from telethon import utils
from telethon.errors.rpcerrorlist import (
    FloodWaitError,
    SlowModeWaitError,
    ChatWriteForbiddenError,
    UserBannedInChannelError,
    ChannelPrivateError,
    ChatAdminRequiredError,
    PeerIdInvalidError,
    UsernameInvalidError,
    UsernameNotOccupiedError,
    InviteRequestSentError,
    InviteHashExpiredError,
    InviteHashInvalidError,
    ChannelsTooMuchError,
)
from telethon.errors import (
    RPCError,
    AuthKeyDuplicatedError,
    AuthKeyUnregisteredError,
    SessionRevokedError,
    UserDeactivatedError,
    UserDeactivatedBanError,
)
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import ImportChatInviteRequest, SetTypingRequest
from telethon.tl.types import Channel, SendMessageTypingAction
from core.client import client, simulate_typing
from core.spintax import SpintaxEngine
from database.client import db, PRIMARY_ACCOUNT

logger = logging.getLogger("PosterEngine")


# The session can never be used again; only a new login helps.
SESSION_DEAD_ERRORS = (
    AuthKeyDuplicatedError,
    AuthKeyUnregisteredError,
    SessionRevokedError,
    UserDeactivatedError,
    UserDeactivatedBanError,
)


def session_dead_reason(exc: Exception) -> str:
    if isinstance(exc, AuthKeyDuplicatedError):
        return ("Telegram анулював сесію: її використовували одночасно з двох IP-адрес "
                "(дві запущені копії програми або перекриття під час деплою). Додайте акаунт повторно.")
    if isinstance(exc, (UserDeactivatedError, UserDeactivatedBanError)):
        return "Telegram-акаунт видалено або заблоковано."
    return "Сесію акаунта завершено в Telegram. Додайте акаунт повторно."


class PostOutcome(Enum):
    SENT = "sent"
    CHAT_UNAVAILABLE = "chat_unavailable"
    DEFERRED = "deferred"
    SYSTEM_ERROR = "system_error"


class PosterWorker:
    """Posting loop of one Telegram account.

    The primary account (default) uses the shared core.client session and the
    chats with account_id = NULL. Additional accounts get their own connected
    client and only see their own chats, rate limits and circuit breaker.
    """

    def __init__(self, account: str = PRIMARY_ACCOUNT, telegram=None, label: str = "Основний акаунт"):
        self.account = account
        self.label = label
        self._telegram = telegram
        self._log = logger if account == PRIMARY_ACCOUNT else logging.getLogger(f"PosterEngine.{label}")
        self._is_active = False
        self._task: asyncio.Task | None = None
        self._consecutive_errors = 0
        self._batch_post_count = 0
        self._wait_reason: str | None = None
        self._wait_until: datetime | None = None
        self._circuit_reason: str | None = None
        self._is_bot_account: bool = False
        self.authorized: bool | None = None
        # Additional accounts: chats already resolved/joined, so ResolveUsername is not repeated every post.
        self._resolved: dict[str, object] = {}

    @property
    def is_primary(self) -> bool:
        return self.account == PRIMARY_ACCOUNT

    @property
    def tg(self):
        # Resolved on every access so the primary worker follows session resets of core.client.
        return self._telegram if self._telegram is not None else client

    def reset_errors(self) -> None:
        self._consecutive_errors = 0
        self._circuit_reason = None
        self._is_bot_account = False

    def get_status(self, settings: dict, authorized: bool, hourly: int, daily: int) -> dict:
        """Report the local worker's actual blockers separately from the master switch."""
        if not settings.get("is_running", False):
            event = db.get_poster_control_event()
            reason = (event or {}).get("details") if (event or {}).get("status") == "circuit_breaker" else None
            return {"state": "paused", "reason": reason or "Розсилку призупинено", "until": None}
        if self._circuit_reason:
            return {"state": "paused", "reason": self._circuit_reason, "until": None}
        if not self._is_active or not self._task or self._task.done():
            return {"state": "stopped", "reason": "Планувальник не запущений", "until": None}
        if not authorized:
            return {"state": "unauthorized", "reason": "Немає підключення або авторизації Telegram", "until": None}
        if self._is_bot_account:
            return {"state": "bot_error", "reason": "⚠️ Авторизовано через Бота, а не через Профіль! Telegram забороняє ботам писати у звичайні групи. Будь ласка, увійдіть через реальний профіль користувача.", "until": None}
        if self._wait_until and self._wait_until > datetime.now(timezone.utc):
            return {"state": "waiting", "reason": self._wait_reason, "until": self._wait_until.isoformat()}
        if self._is_night_time(settings):
            return {"state": "night", "reason": "Нічна пауза", "until": None}
        if hourly >= settings.get("max_posts_per_hour", 15):
            return {"state": "hourly_limit", "reason": "Досягнуто погодинного ліміту", "until": None}
        if daily >= settings.get("max_posts_per_day", 150):
            return {"state": "daily_limit", "reason": "Досягнуто добового ліміту", "until": None}
        return {"state": "running", "reason": None, "until": None}

    async def _wait(self, seconds: int, reason: str) -> None:
        self._wait_reason = reason
        self._wait_until = datetime.now(timezone.utc) + timedelta(seconds=seconds)
        try:
            await asyncio.sleep(seconds)
        finally:
            self._wait_reason = None
            self._wait_until = None

    async def start(self):
        """Start the background posting loop."""
        if self._is_active:
            return
        self._is_active = True
        self._log.info("Auto-Poster Worker loop started with Advanced Anti-Ban protections.")
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self):
        """Stop the background posting loop."""
        self._is_active = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._log.info("Auto-Poster Worker loop stopped.")

    def _is_night_time(self, settings: dict) -> bool:
        """Check if current time falls within night sleep hours."""
        if not settings.get("enable_night_mode", False):
            return False

        now = datetime.now()
        cur_hour = now.hour
        start = settings.get("night_start_hour", 23)
        end = settings.get("night_end_hour", 8)

        if start > end:
            # Over midnight (e.g. 23:00 to 08:00)
            return cur_hour >= start or cur_hour < end
        else:
            return start <= cur_hour < end

    def _trip_circuit_breaker(self, reason: str) -> None:
        # Keep this process paused even if persistence is unavailable.
        self._circuit_reason = reason
        if self.is_primary:
            db.toggle_poster(False, reason=reason)
            return
        # An additional account pauses only itself; the other accounts keep posting.
        db.update_account(self.account, {"posting_enabled": False, "status": "error", "last_error": reason})
        db.add_log("SYSTEM", "account_paused", f"{self.label}: {reason}", account=self.account)

    async def _ensure_authorized(self) -> bool:
        tg = self.tg
        if not self.is_primary and not tg.is_connected():
            try:
                await tg.connect()
            except SESSION_DEAD_ERRORS as dead:
                self._mark_session_dead(session_dead_reason(dead))
                return False
            except Exception as conn_err:
                self._log.warning(f"Reconnect failed: {conn_err}")
        if tg.is_connected() and await tg.is_user_authorized():
            self.authorized = True
            return True
        self.authorized = False
        if not self.is_primary and tg.is_connected():
            # Connected but not authorized: the session was terminated in Telegram settings.
            self._mark_session_dead("Сесію акаунта завершено в Telegram. Додайте акаунт повторно.")
        return False

    def _mark_session_dead(self, reason: str) -> None:
        """Stop an additional account for good; the supervisor disconnects it once posting is disabled."""
        self.authorized = False
        self._circuit_reason = reason
        self._log.error(f"Session is no longer usable: {reason}")
        db.update_account(self.account, {"posting_enabled": False, "status": "unauthorized", "last_error": reason})
        db.add_log("SYSTEM", "account_paused", f"{self.label}: {reason}", account=self.account)

    async def _run_loop(self):
        """Main polling, rate-limiting, and posting cycle."""
        while self._is_active:
            try:
                # 1. Check global master switch
                settings = db.get_settings()
                if not settings.get("is_running", False):
                    self.reset_errors()
                    await asyncio.sleep(5)
                    continue
                if self._circuit_reason:
                    await asyncio.sleep(5)
                    continue

                # 2. Night sleep mode protection
                if self._is_night_time(settings):
                    self._log.info("🌙 Night sleep mode active. Sleeping 15 minutes to prevent bot-like night activity...")
                    await self._wait(900, "Нічна пауза")
                    continue

                # 3. Check if user is connected and authorized
                if not await self._ensure_authorized():
                    self._log.warning("Poster idle: Telegram user is not connected or not authorized.")
                    await asyncio.sleep(15)
                    continue

                # 3.1 Check if account is a bot
                try:
                    me = await self.tg.get_me()
                    if getattr(me, "bot", False):
                        self._is_bot_account = True
                        self._log.error(
                            "❌ КРИТИЧНО: Клієнт авторизований як БОТ (@%s). Боти не мають змоги розсилати у звичайні групи! Розсилку призупинено. Авторизуйтесь під профілем.",
                            getattr(me, "username", me.id)
                        )
                        await self._wait(60, "Авторизовано через Бота — потрібен вхід під профілем")
                        continue
                    else:
                        self._is_bot_account = False
                except Exception as me_err:
                    self._log.warning(f"Error checking bot status in poster: {me_err}")

                # 4. Hourly & Daily Rate Limit Safety Checks (per account)
                max_hourly = settings.get("max_posts_per_hour", 15)
                cur_hourly = db.get_hourly_post_count(self.account)
                if cur_hourly >= max_hourly:
                    self._log.warning(f"🛡️ Safety throttle: Hourly limit reached ({cur_hourly}/{max_hourly} posts). Resting 5 min...")
                    await self._wait(300, "Досягнуто погодинного ліміту")
                    continue

                max_daily = settings.get("max_posts_per_day", 150)
                cur_daily = db.get_daily_post_count(self.account)
                if cur_daily >= max_daily:
                    self._log.warning(f"🛡️ Safety throttle: Daily limit reached ({cur_daily}/{max_daily} posts). Resting 30 min...")
                    await self._wait(1800, "Досягнуто добового ліміту")
                    continue

                # 5. Batch Rest Cooldown (Humanization)
                batch_size = settings.get("batch_size", 6)
                batch_rest_min = settings.get("batch_rest_minutes", 8)
                if self._batch_post_count >= batch_size:
                    self._log.info(f"☕ Batch of {self._batch_post_count} posts completed. Taking a human rest pause for {batch_rest_min} minutes...")
                    db.add_log("SYSTEM", "security_pause", f"Захисна пауза після {self._batch_post_count} постів на {batch_rest_min} хв", account=self.account)
                    self._batch_post_count = 0
                    await self._wait(batch_rest_min * 60, "Захисна пауза між серіями публікацій")
                    continue

                # 6. Fetch chats of this account that are due for posting
                due_chats = db.get_due_chats(self.account)
                if not due_chats:
                    await asyncio.sleep(10)
                    continue

                self._log.info(f"Found {len(due_chats)} chats ready for posting.")

                # Fetch default fallback post
                default_post = db.get_active_post()

                # 7. Process due chats sequentially with human delays & protections
                for chat in due_chats:
                    fresh_settings = db.get_settings()
                    if not fresh_settings.get("is_running", False) or not self._is_active:
                        self._log.info("Posting loop paused by user.")
                        break

                    # Check hourly cap mid-batch
                    if db.get_hourly_post_count(self.account) >= fresh_settings.get("max_posts_per_hour", 15):
                        self._log.warning("Hourly limit reached mid-batch. Pausing...")
                        break
                    if db.get_daily_post_count(self.account) >= fresh_settings.get("max_posts_per_day", 150):
                        break
                    if self._batch_post_count >= fresh_settings.get("batch_size", 6):
                        break
                    if self._is_night_time(fresh_settings):
                        break

                    # Determine which post variant to use for this specific chat
                    chat_post_id = chat.get("post_id")
                    target_post = None
                    if chat_post_id:
                        target_post = db.get_post_by_id(chat_post_id)

                    if not target_post:
                        target_post = default_post

                    if not target_post or not target_post.get("content"):
                        self._log.warning(f"Skipping '{chat.get('chat_peer')}': No active post content found.")
                        continue

                    base_ad_text = target_post["content"]

                    # Generate dynamic text per chat using Spintax + Anti-fingerprint
                    final_text = base_ad_text
                    if fresh_settings.get("enable_spintax", True):
                        final_text = SpintaxEngine.parse(final_text)
                    if fresh_settings.get("enable_anti_fingerprint", True):
                        final_text = SpintaxEngine.inject_anti_fingerprint(final_text, True)

                    # Post with full error handling, native custom emojis, and typing simulation
                    outcome = await self._post_to_chat(chat, final_text, fresh_settings, target_post)

                    if outcome is PostOutcome.SENT:
                        self._batch_post_count += 1
                        self._consecutive_errors = 0
                    elif outcome is PostOutcome.SYSTEM_ERROR:
                        self._consecutive_errors += 1
                        # Circuit breaker
                        if fresh_settings.get("auto_circuit_breaker", True) and self._consecutive_errors >= 3:
                            self._log.error("🚨 CIRCUIT BREAKER TRIGGERED: 3 consecutive errors! Auto-pausing poster for safety.")
                            reason = "Автоматична аварійна пауза: 3 системні помилки поспіль. Перевірте журнал перед відновленням."
                            self._consecutive_errors = 0
                            self._trip_circuit_breaker(reason)
                            break

                    # Random human-like delay between consecutive chats
                    min_delay = fresh_settings.get("min_delay_seconds", 15)
                    max_delay = fresh_settings.get("max_delay_seconds", 35)
                    delay = random.randint(min(min_delay, max_delay), max(min_delay, max_delay))

                    self._log.info(f"Waiting {delay}s before next chat to emulate human behavior...")
                    await self._wait(delay, "Інтервал між публікаціями")

            except asyncio.CancelledError:
                break
            except Exception as e:
                if not self.is_primary and isinstance(e, SESSION_DEAD_ERRORS):
                    self._mark_session_dead(session_dead_reason(e))
                    continue
                self._log.error(f"Unexpected error in Poster loop: {e}", exc_info=True)
                await self._wait(10, "Помилка планувальника — повторна перевірка")

    async def _resolve_peer(self, chat_peer: str):
        """Resolve the chat; additional accounts join it first, since they were usually never members."""
        tg = self.tg
        if self.is_primary:
            return await tg.get_input_entity(chat_peer)
        if chat_peer not in self._resolved:
            self._resolved[chat_peer] = await self._join_and_resolve(chat_peer)
        return self._resolved[chat_peer]

    async def _join_and_resolve(self, chat_peer: str):
        tg = self.tg
        if chat_peer.startswith("+") and not chat_peer[1:].isdigit():
            # Batch import stores invite links as "+HASH"; Telethon only recognises the full link.
            chat_peer = f"https://t.me/{chat_peer}"
        invite_hash, is_invite = utils.parse_username(chat_peer)
        if is_invite:
            try:
                return await tg.get_input_entity(chat_peer)
            except ValueError:
                updates = await tg(ImportChatInviteRequest(invite_hash))
                self._log.info(f"Joined '{chat_peer}' by invite link before posting.")
                await self._wait(random.randint(5, 15), "Вступ у чат перед публікацією")
                return utils.get_input_peer(updates.chats[0])

        entity = await tg.get_entity(chat_peer)
        if isinstance(entity, Channel) and entity.left:
            await tg(JoinChannelRequest(entity))
            self._log.info(f"Joined '{chat_peer}' before posting.")
            await self._wait(random.randint(5, 15), "Вступ у чат перед публікацією")
        return utils.get_input_peer(entity)

    async def _simulate_typing(self, peer, duration_seconds: int) -> None:
        if self.is_primary:
            await simulate_typing(peer, duration_seconds=duration_seconds)
            return
        try:
            await self.tg(SetTypingRequest(peer=peer, action=SendMessageTypingAction()))
            await asyncio.sleep(duration_seconds)
        except (FloodWaitError, SlowModeWaitError):
            raise
        except Exception:
            pass

    async def _post_to_chat(self, chat: dict, ad_text: str, settings: dict, active_post: dict | None = None) -> PostOutcome:
        outcome = await self._send_to_chat(chat, ad_text, settings, active_post)
        if outcome in (PostOutcome.CHAT_UNAVAILABLE, PostOutcome.SYSTEM_ERROR):
            # Membership may have changed (kicked, banned, link revoked): resolve and re-join next time.
            self._resolved.pop(chat["chat_peer"], None)
        return outcome

    async def _send_to_chat(self, chat: dict, ad_text: str, settings: dict, active_post: dict | None = None) -> PostOutcome:
        """Post the message to a specific chat with comprehensive error handling & typing simulation."""
        chat_id = chat["id"]
        chat_peer = chat["chat_peer"]
        interval = chat.get("interval_minutes", 60)
        jitter_minutes = settings.get("jitter_minutes", 3)
        tg = self.tg
        account = self.account

        self._log.info(f"Preparing post to '{chat_peer}' (Interval: {interval}m)...")

        try:
            # 1. Resolve peer
            try:
                peer = await self._resolve_peer(chat_peer)
            except ValueError:
                db.update_chat(chat_id, {"status": "error", "is_active": False, "last_error": "Не вдалося знайти чат за посиланням"})
                db.add_log(chat_peer, "failed", "Не вдалося знайти чат за посиланням", account=account)
                return PostOutcome.CHAT_UNAVAILABLE

            # 2. Simulate human typing action if enabled
            if settings.get("enable_typing_simulation", True):
                typing_sec = settings.get("typing_duration_seconds", 4)
                await self._simulate_typing(peer, duration_seconds=typing_sec)

            # 3. Check if we have exact source Telegram message with Premium Custom Emojis.
            # The source lives in the primary account's Saved Messages, so other accounts send the stored HTML.
            source_msg_id = (active_post or {}).get("source_msg_id") if self.is_primary else None
            source_chat = (active_post or {}).get("source_chat_peer", "me")
            sent = False

            if source_msg_id:
                # Fetch original Telegram message to keep 100% native animated emojis, media, and formatting
                src_msg = None
                try:
                    src_msg = await tg.get_messages(source_chat, ids=int(source_msg_id))
                except (ValueError, PeerIdInvalidError, ChannelPrivateError, ChatAdminRequiredError) as src_err:
                    self._log.warning(f"Could not use raw source message {source_msg_id}, falling back to text: {src_err}")
                if src_msg and (src_msg.message or src_msg.text):
                    await tg.send_message(
                        peer,
                        message=src_msg.message or src_msg.text,
                        formatting_entities=src_msg.entities,
                        file=src_msg.media
                    )
                    sent = True

            if not sent:
                # Send with HTML formatting (supports Telegram Premium tags <tg-emoji> and standard formatting)
                self._log.info(f"Sending formatted HTML text to '{chat_peer}'...")
                await tg.send_message(peer, ad_text, parse_mode="html")

            # 4. Calculate jitter and update success
            jitter_sec = random.randint(0, max(1, jitter_minutes * 60))
            db.update_chat_post_success(chat_id, interval, jitter_sec)
            db.add_log(chat_peer, "success", f"Успішно опубліковано. Наступний через {interval}хв + {jitter_sec}с", account=account)
            self._log.info(f"✅ Successfully posted to '{chat_peer}'.")
            return PostOutcome.SENT

        except SlowModeWaitError as e:
            wait_seconds = getattr(e, "seconds", 60)
            self._log.warning(f"⏳ SlowMode in '{chat_peer}': must wait {wait_seconds}s.")
            db.update_chat_delay(chat_id, wait_seconds + 5, "slowmode_wait", f"Повільний режим: чекати {wait_seconds}с")
            db.add_log(chat_peer, "slowmode_wait", f"Slowmode {wait_seconds}s", account=account)
            return PostOutcome.DEFERRED

        except FloodWaitError as e:
            wait_seconds = getattr(e, "seconds", 60)
            self._log.error(f"🚨 Telegram FloodWait: {wait_seconds}s required. Sleeping...")
            db.update_chat_delay(chat_id, wait_seconds + 15, "flood_wait", f"FloodWait: {wait_seconds}с")
            db.add_log(chat_peer, "flood_wait", f"FloodWait {wait_seconds}s", account=account)
            await self._wait(wait_seconds + 2, "Обмеження Telegram (FloodWait)")
            return PostOutcome.DEFERRED

        except InviteRequestSentError:
            err_msg = "Заявку на вступ надіслано — очікуємо схвалення адміністратора"
            self._log.warning(f"📨 Join request sent to '{chat_peer}'.")
            db.update_chat_delay(chat_id, 6 * 3600, "restricted", err_msg)
            db.add_log(chat_peer, "failed", err_msg, account=account)
            return PostOutcome.DEFERRED

        except ChannelsTooMuchError:
            err_msg = "Акаунт досяг ліміту Telegram на кількість чатів — вийдіть із зайвих"
            self._log.warning(f"🚫 Too many channels joined, cannot join '{chat_peer}'.")
            db.update_chat_delay(chat_id, 3600, "error", err_msg)
            db.add_log(chat_peer, "failed", err_msg, account=account)
            return PostOutcome.DEFERRED

        except (InviteHashExpiredError, InviteHashInvalidError):
            err_msg = "Посилання-запрошення недійсне або прострочене"
            self._log.warning(f"🔗 Invalid invite link: '{chat_peer}'")
            db.update_chat(chat_id, {"status": "error", "is_active": False, "last_error": err_msg})
            db.add_log(chat_peer, "failed", err_msg, account=account)
            return PostOutcome.CHAT_UNAVAILABLE

        except (ChatWriteForbiddenError, UserBannedInChannelError) as e:
            err_msg = "Заборонено писати в чат або бан"
            self._log.warning(f"🚫 Cannot write to '{chat_peer}': {e}")
            db.update_chat(chat_id, {"status": "restricted", "is_active": False, "last_error": err_msg})
            db.add_log(chat_peer, "failed", f"Обмеження: {err_msg}", account=account)
            return PostOutcome.CHAT_UNAVAILABLE

        except (ChannelPrivateError, ChatAdminRequiredError) as e:
            err_msg = "Чат приватний або вимагає прав адміна"
            self._log.warning(f"🔒 Access issue with '{chat_peer}': {e}")
            db.update_chat(chat_id, {"status": "error", "is_active": False, "last_error": err_msg})
            db.add_log(chat_peer, "failed", f"Доступ: {err_msg}", account=account)
            return PostOutcome.CHAT_UNAVAILABLE

        except (PeerIdInvalidError, UsernameInvalidError, UsernameNotOccupiedError):
            err_msg = "Невірний username або посилання"
            self._log.error(f"❌ Invalid peer: '{chat_peer}'")
            db.update_chat(chat_id, {"status": "error", "is_active": False, "last_error": err_msg})
            db.add_log(chat_peer, "failed", err_msg, account=account)
            return PostOutcome.CHAT_UNAVAILABLE

        except RPCError as e:
            if not self.is_primary and isinstance(e, SESSION_DEAD_ERRORS):
                raise  # An account problem, not a chat problem: the loop stops this account.
            err_text = str(e)
            if "SLOWMODE_WAIT" in err_text:
                wait_sec = getattr(e, "seconds", 60)
                db.update_chat_delay(chat_id, wait_sec + 5, "slowmode_wait", f"Slowmode: {wait_sec}с")
                db.add_log(chat_peer, "slowmode_wait", f"Slowmode {wait_sec}s", account=account)
                return PostOutcome.DEFERRED
            elif "FLOOD_WAIT" in err_text:
                wait_sec = getattr(e, "seconds", 60)
                db.update_chat_delay(chat_id, wait_sec + 15, "flood_wait", f"FloodWait: {wait_sec}с")
                db.add_log(chat_peer, "flood_wait", f"FloodWait {wait_sec}s", account=account)
                await self._wait(wait_sec + 2, "Обмеження Telegram (FloodWait)")
                return PostOutcome.DEFERRED
            else:
                self._log.error(f"❌ RPCError on '{chat_peer}': {e}")
                db.update_chat_delay(chat_id, 900, "error", err_text)
                db.add_log(chat_peer, "failed", f"RPC Помилка: {err_text}", account=account)
            return PostOutcome.SYSTEM_ERROR

        except Exception as e:
            self._log.error(f"❌ Failed to post to '{chat_peer}': {e}")
            db.update_chat_delay(chat_id, 900, "error", str(e))
            db.add_log(chat_peer, "failed", f"Помилка: {str(e)}", account=account)
            return PostOutcome.SYSTEM_ERROR


poster_worker = PosterWorker()
