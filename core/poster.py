import asyncio
import random
import logging
from datetime import datetime, timezone
from telethon.errors.rpcerrorlist import (
    FloodWaitError,
    SlowModeWaitError,
    ChatWriteForbiddenError,
    UserBannedInChannelError,
    ChannelPrivateError,
    ChatAdminRequiredError,
    PeerIdInvalidError
)
from telethon.errors import RPCError
from core.client import client, simulate_typing
from core.spintax import SpintaxEngine
from database.client import db

logger = logging.getLogger("PosterEngine")


class PosterWorker:
    def __init__(self):
        self._is_active = False
        self._task: asyncio.Task | None = None
        self._consecutive_errors = 0
        self._batch_post_count = 0

    async def start(self):
        """Start the background posting loop."""
        if self._is_active:
            return
        self._is_active = True
        logger.info("Auto-Poster Worker loop started with Advanced Anti-Ban protections.")
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
        logger.info("Auto-Poster Worker loop stopped.")

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

    async def _run_loop(self):
        """Main polling, rate-limiting, and posting cycle."""
        while self._is_active:
            try:
                # 1. Check global master switch
                settings = db.get_settings()
                if not settings.get("is_running", False):
                    await asyncio.sleep(5)
                    continue

                # 2. Night sleep mode protection
                if self._is_night_time(settings):
                    logger.info("🌙 Night sleep mode active. Sleeping 15 minutes to prevent bot-like night activity...")
                    await asyncio.sleep(900)
                    continue

                # 3. Check if user is connected and authorized
                if not client.is_connected() or not await client.is_user_authorized():
                    logger.warning("Poster idle: Telegram user is not connected or not authorized.")
                    await asyncio.sleep(15)
                    continue

                # 4. Hourly & Daily Rate Limit Safety Checks
                max_hourly = settings.get("max_posts_per_hour", 15)
                cur_hourly = db.get_hourly_post_count()
                if cur_hourly >= max_hourly:
                    logger.warning(f"🛡️ Safety throttle: Hourly limit reached ({cur_hourly}/{max_hourly} posts). Resting 5 min...")
                    await asyncio.sleep(300)
                    continue

                max_daily = settings.get("max_posts_per_day", 150)
                cur_daily = db.get_daily_post_count()
                if cur_daily >= max_daily:
                    logger.warning(f"🛡️ Safety throttle: Daily limit reached ({cur_daily}/{max_daily} posts). Resting 30 min...")
                    await asyncio.sleep(1800)
                    continue

                # 5. Batch Rest Cooldown (Humanization)
                batch_size = settings.get("batch_size", 6)
                batch_rest_min = settings.get("batch_rest_minutes", 8)
                if self._batch_post_count >= batch_size:
                    logger.info(f"☕ Batch of {self._batch_post_count} posts completed. Taking a human rest pause for {batch_rest_min} minutes...")
                    db.add_log("SYSTEM", "security_pause", f"Захисна пауза після {self._batch_post_count} постів на {batch_rest_min} хв")
                    self._batch_post_count = 0
                    await asyncio.sleep(batch_rest_min * 60)
                    continue

                # 6. Fetch chats that are due for posting
                due_chats = db.get_due_chats()
                if not due_chats:
                    await asyncio.sleep(10)
                    continue

                logger.info(f"Found {len(due_chats)} chats ready for posting.")

                # Fetch default fallback post
                default_post = db.get_active_post()

                # 7. Process due chats sequentially with human delays & protections
                for chat in due_chats:
                    fresh_settings = db.get_settings()
                    if not fresh_settings.get("is_running", False) or not self._is_active:
                        logger.info("Posting loop paused by user.")
                        break

                    # Check hourly cap mid-batch
                    if db.get_hourly_post_count() >= fresh_settings.get("max_posts_per_hour", 15):
                        logger.warning("Hourly limit reached mid-batch. Pausing...")
                        break

                    # Determine which post variant to use for this specific chat
                    chat_post_id = chat.get("post_id")
                    target_post = None
                    if chat_post_id:
                        target_post = db.get_post_by_id(chat_post_id)

                    if not target_post:
                        target_post = default_post

                    if not target_post or not target_post.get("content"):
                        logger.warning(f"Skipping '{chat.get('chat_peer')}': No active post content found.")
                        continue

                    base_ad_text = target_post["content"]

                    # Generate dynamic text per chat using Spintax + Anti-fingerprint
                    final_text = base_ad_text
                    if fresh_settings.get("enable_spintax", True):
                        final_text = SpintaxEngine.parse(final_text)
                    if fresh_settings.get("enable_anti_fingerprint", True):
                        final_text = SpintaxEngine.inject_anti_fingerprint(final_text, True)

                    # Post with full error handling, native custom emojis, and typing simulation
                    success = await self._post_to_chat(chat, final_text, fresh_settings, target_post)
                    
                    if success:
                        self._batch_post_count += 1
                        self._consecutive_errors = 0
                    else:
                        self._consecutive_errors += 1
                        # Circuit breaker
                        if fresh_settings.get("auto_circuit_breaker", True) and self._consecutive_errors >= 3:
                            logger.error("🚨 CIRCUIT BREAKER TRIGGERED: 3 consecutive errors! Auto-pausing poster for safety.")
                            db.toggle_poster(False)
                            db.add_log("SECURITY", "circuit_breaker", "Автоматична аварійна пауза: виявлено 3 помилки поспіль для захисту акаунту.")
                            break

                    # Random human-like delay between consecutive chats
                    min_delay = fresh_settings.get("min_delay_seconds", 15)
                    max_delay = fresh_settings.get("max_delay_seconds", 35)
                    delay = random.randint(min(min_delay, max_delay), max(min_delay, max_delay))
                    
                    logger.info(f"Waiting {delay}s before next chat to emulate human behavior...")
                    await asyncio.sleep(delay)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Unexpected error in Poster loop: {e}", exc_info=True)
                await asyncio.sleep(10)

    async def _post_to_chat(self, chat: dict, ad_text: str, settings: dict, active_post: dict | None = None) -> bool:
        """Post the message to a specific chat with comprehensive error handling & typing simulation."""
        chat_id = chat["id"]
        chat_peer = chat["chat_peer"]
        interval = chat.get("interval_minutes", 60)
        jitter_minutes = settings.get("jitter_minutes", 3)

        logger.info(f"Preparing post to '{chat_peer}' (Interval: {interval}m)...")

        try:
            # 1. Resolve peer
            peer = await client.get_input_entity(chat_peer)

            # 2. Simulate human typing action if enabled
            if settings.get("enable_typing_simulation", True):
                typing_sec = settings.get("typing_duration_seconds", 4)
                await simulate_typing(peer, duration_seconds=typing_sec)

            # 3. Check if we have exact source Telegram message with Premium Custom Emojis
            source_msg_id = (active_post or {}).get("source_msg_id")
            source_chat = (active_post or {}).get("source_chat_peer", "me")
            sent = False

            if source_msg_id:
                # Fetch original Telegram message to keep 100% native animated emojis, media, and formatting
                try:
                    src_msg = await client.get_messages(source_chat, ids=int(source_msg_id))
                    if src_msg and (src_msg.message or src_msg.text):
                        logger.info(f"✨ Sending native Telegram message #{source_msg_id} with {len(src_msg.entities or [])} entities & Premium Emojis")
                        await client.send_message(
                            peer,
                            message=src_msg.message or src_msg.text,
                            formatting_entities=src_msg.entities,
                            file=src_msg.media
                        )
                        sent = True
                except Exception as src_err:
                    logger.warning(f"Could not use raw source message {source_msg_id}, falling back to text: {src_err}")

            if not sent:
                # Send with HTML formatting (supports Telegram Premium tags <tg-emoji> and standard formatting)
                logger.info(f"Sending formatted HTML text to '{chat_peer}'...")
                await client.send_message(peer, ad_text, parse_mode="html")

            # 4. Calculate jitter and update success
            jitter_sec = random.randint(0, max(1, jitter_minutes * 60))
            db.update_chat_post_success(chat_id, interval, jitter_sec)
            db.add_log(chat_peer, "success", f"Успішно опубліковано. Наступний через {interval}хв + {jitter_sec}с")
            logger.info(f"✅ Successfully posted to '{chat_peer}'.")
            return True

        except SlowModeWaitError as e:
            wait_seconds = getattr(e, "seconds", 60)
            logger.warning(f"⏳ SlowMode in '{chat_peer}': must wait {wait_seconds}s.")
            db.update_chat_delay(chat_id, wait_seconds + 5, "slowmode_wait", f"Повільний режим: чекати {wait_seconds}с")
            db.add_log(chat_peer, "slowmode_wait", f"Slowmode {wait_seconds}s")
            return False

        except FloodWaitError as e:
            wait_seconds = getattr(e, "seconds", 60)
            logger.error(f"🚨 Telegram FloodWait: {wait_seconds}s required. Sleeping...")
            db.update_chat_delay(chat_id, wait_seconds + 15, "flood_wait", f"FloodWait: {wait_seconds}с")
            db.add_log(chat_peer, "flood_wait", f"FloodWait {wait_seconds}s")
            await asyncio.sleep(wait_seconds + 2)
            return False

        except (ChatWriteForbiddenError, UserBannedInChannelError) as e:
            err_msg = "Заборонено писати в чат або бан"
            logger.warning(f"🚫 Cannot write to '{chat_peer}': {e}")
            db.update_chat(chat_id, {"status": "restricted", "is_active": False, "last_error": err_msg})
            db.add_log(chat_peer, "failed", f"Обмеження: {err_msg}")
            return False

        except (ChannelPrivateError, ChatAdminRequiredError) as e:
            err_msg = "Чат приватний або вимагає прав адміна"
            logger.warning(f"🔒 Access issue with '{chat_peer}': {e}")
            db.update_chat(chat_id, {"status": "error", "is_active": False, "last_error": err_msg})
            db.add_log(chat_peer, "failed", f"Доступ: {err_msg}")
            return False

        except PeerIdInvalidError:
            err_msg = "Невірний username або посилання"
            logger.error(f"❌ Invalid peer: '{chat_peer}'")
            db.update_chat(chat_id, {"status": "error", "is_active": False, "last_error": err_msg})
            db.add_log(chat_peer, "failed", err_msg)
            return False

        except RPCError as e:
            err_text = str(e)
            if "SLOWMODE_WAIT" in err_text:
                wait_sec = getattr(e, "seconds", 60)
                db.update_chat_delay(chat_id, wait_sec + 5, "slowmode_wait", f"Slowmode: {wait_sec}с")
                db.add_log(chat_peer, "slowmode_wait", f"Slowmode {wait_sec}s")
            elif "FLOOD_WAIT" in err_text:
                wait_sec = getattr(e, "seconds", 60)
                db.update_chat_delay(chat_id, wait_sec + 15, "flood_wait", f"FloodWait: {wait_sec}с")
                db.add_log(chat_peer, "flood_wait", f"FloodWait {wait_sec}s")
                await asyncio.sleep(wait_sec + 2)
            else:
                logger.error(f"❌ RPCError on '{chat_peer}': {e}")
                db.update_chat_delay(chat_id, 900, "error", err_text)
                db.add_log(chat_peer, "failed", f"RPC Помилка: {err_text}")
            return False

        except Exception as e:
            logger.error(f"❌ Failed to post to '{chat_peer}': {e}")
            db.update_chat_delay(chat_id, 900, "error", str(e))
            db.add_log(chat_peer, "failed", f"Помилка: {str(e)}")
            return False


poster_worker = PosterWorker()
