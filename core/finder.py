"""Telegram discovery transport. Searching never joins groups or sends messages."""

import asyncio
import logging
import random
import time
from datetime import timezone
from typing import Any, AsyncIterator, Awaitable, Callable

from telethon import errors, utils
from telethon.tl.functions.channels import GetFullChannelRequest, JoinChannelRequest
from telethon.tl.functions.contacts import SearchRequest
from telethon.tl.functions.messages import SearchGlobalRequest
from telethon.tl.types import Channel, InputMessagesFilterEmpty, InputPeerEmpty

from config import config
from core.client import client
from core.discovery_relevance import clean_text
from database.client import db

logger = logging.getLogger("ChatFinder")


class TelegramDiscovery:
    def __init__(self, telegram: Any, on_wait: Callable[[int, str], Awaitable[None]]) -> None:
        self.client = telegram
        self.on_wait = on_wait
        self._next_call = 0.0
        self._lock = asyncio.Lock()

    async def ready(self) -> bool:
        return self.client.is_connected() and await self.client.is_user_authorized()

    async def call(self, operation: Callable[[], Awaitable[Any]]) -> Any:
        async with self._lock:
            failures = 0
            waited = False
            while True:
                await asyncio.sleep(max(0, self._next_call - time.monotonic()))
                try:
                    result = await asyncio.wait_for(operation(), timeout=40)
                    if waited:
                        await self.on_wait(0, "З’єднання відновлено. Продовжуємо пошук.")
                    return result
                except errors.FloodWaitError as exc:
                    delay = exc.seconds + 3
                    await self.on_wait(delay, "Telegram обмежив запити. Очікуємо дозволеного часу.")
                    await asyncio.sleep(delay)
                    waited = True
                except (OSError, asyncio.TimeoutError, errors.ServerError):
                    failures += 1
                    delay = min(300, 30 * 2 ** min(failures - 1, 4))
                    await self.on_wait(delay, "Тимчасова помилка Telegram. Повторимо запит після паузи.")
                    await asyncio.sleep(delay)
                    waited = True
                finally:
                    self._next_call = time.monotonic() + random.uniform(
                        config.DISCOVERY_RPC_MIN_SECONDS, config.DISCOVERY_RPC_MAX_SECONDS,
                    )

    @staticmethod
    def parse(chat: Any, query: str) -> dict[str, Any] | None:
        if not isinstance(chat, Channel) or not chat.username:
            return None
        group = bool(chat.megagroup and not getattr(chat, "gigagroup", False))
        rights = getattr(chat, "default_banned_rights", None)
        restricted = bool(rights and (rights.send_messages or rights.view_messages or getattr(rights, "send_plain", False)))
        own_rights = getattr(chat, "banned_rights", None)
        restricted = restricted or bool(own_rights and (own_rights.send_messages or own_rights.view_messages or getattr(own_rights, "send_plain", False)))
        access = "restricted" if not group or restricted else "join_required" if chat.left else "writable"
        if getattr(chat, "join_request", False) and chat.left:
            access = "approval_required"
        return {
            "telegram_id": utils.get_peer_id(chat), "peer": f"@{chat.username}",
            "title": clean_text(chat.title), "type": "group" if group else "channel",
            "participants_count": getattr(chat, "participants_count", None),
            "can_post": access == "writable", "posting_access": access,
            "matched_query": query, "description": "", "messages": [], "last_message_at": None,
            "_entity": chat,
        }

    async def search(self, query: str, cursor: dict[str, Any]) -> AsyncIterator[tuple[list[dict[str, Any]], dict[str, Any] | None]]:
        directory = await self.call(lambda: self.client(SearchRequest(q=query, limit=50), flood_sleep_threshold=0))
        yield [c for chat in directory.chats if (c := self.parse(chat, query))], None
        offset_peer: Any = InputPeerEmpty()
        if cursor.get("peer"):
            try:
                offset_peer = await self.call(lambda: self.client.get_input_entity(cursor["peer"]))
            except (ValueError, errors.UsernameInvalidError, errors.UsernameNotOccupiedError, errors.ChannelPrivateError):
                cursor = {}
        offset_id, offset_rate = cursor.get("id", 0), cursor.get("rate", 0)
        # Visit a bounded number of pages per query; persist the cursor for the next cycle.
        for _ in range(2):
            response = await self.call(lambda: self.client(SearchGlobalRequest(
                q=query, filter=InputMessagesFilterEmpty(), min_date=None, max_date=None,
                offset_rate=offset_rate, offset_peer=offset_peer, offset_id=offset_id,
                limit=30, groups_only=True,
            ), flood_sleep_threshold=0))
            messages = [m for m in response.messages if getattr(m, "peer_id", None) and getattr(m, "date", None)]
            peers = {utils.get_peer_id(m.peer_id) for m in messages if getattr(m, "peer_id", None)}
            chats = {utils.get_peer_id(chat): chat for chat in response.chats}
            candidates = [c for key, chat in chats.items() if key in peers and (c := self.parse(chat, query))]
            if not messages:
                yield candidates, {}
                return
            last = messages[-1]
            entity = chats.get(utils.get_peer_id(last.peer_id))
            if not entity or not getattr(entity, "username", None):
                yield candidates, {}
                return
            new_rate = getattr(response, "next_rate", None) or int(last.date.timestamp())
            new_cursor = {"peer": f"@{entity.username}", "id": last.id, "rate": new_rate}
            if new_cursor == cursor:
                yield candidates, {}
                return
            yield candidates, new_cursor
            cursor = new_cursor
            offset_peer = utils.get_input_peer(entity)
            offset_id, offset_rate = last.id, new_rate

    async def enrich(self, candidate: dict[str, Any]) -> dict[str, Any]:
        if candidate["type"] != "group":
            return candidate
        entity = candidate.get("_entity")
        if entity is None:
            try:
                entity = await self.call(lambda: self.client.get_entity(candidate["telegram_id"]))
            except ValueError:
                entity = await self.call(lambda: self.client.get_entity(candidate["peer"]))
            if utils.get_peer_id(entity) != candidate["telegram_id"]:
                # A username may now belong to a different group; do not mix their evidence.
                return candidate
        inaccessible = (errors.ChannelPrivateError, errors.ChatAdminRequiredError, errors.UserBannedInChannelError)
        try:
            full = await self.call(lambda: self.client(GetFullChannelRequest(entity), flood_sleep_threshold=0))
            candidate["description"] = clean_text(full.full_chat.about or "")[:2000]
            count = getattr(full.full_chat, "participants_count", None)
            if count is not None:
                candidate["participants_count"] = count
        except inaccessible:
            candidate["posting_access"] = "unknown"
            candidate["can_post"] = False
        try:
            messages = await self.call(lambda: self.client.get_messages(entity, limit=30))
            candidate["messages"] = [clean_text(m.message)[:700] for m in messages if getattr(m, "message", None)]
            dated = [m for m in messages if getattr(m, "date", None)]
            if dated:
                candidate["last_message_at"] = max(m.date for m in dated).astimezone(timezone.utc).isoformat()
        except inaccessible:
            pass
        return candidate


class ChatFinder:
    @staticmethod
    async def join_and_add_chat(peer_str: str, interval_minutes: int = 60, title: str = "",
                                post_id: str | None = None) -> dict[str, Any]:
        """Explicit user action only; never called by the discovery worker."""
        if not client.is_connected() or not await client.is_user_authorized():
            return {"status": "error", "message": "Telegram клієнт не авторизований"}
        peer_str = peer_str.strip()
        if not peer_str.startswith(("@", "http", "-")):
            peer_str = f"@{peer_str}"
        try:
            entity = await client.get_entity(peer_str)
            if isinstance(entity, Channel) and entity.left:
                await client(JoinChannelRequest(entity), flood_sleep_threshold=0)
            saved = await asyncio.to_thread(db.add_chat, peer_str, interval_minutes=interval_minutes,
                                            title=getattr(entity, "title", title or peer_str), post_id=post_id)
            if not saved:
                return {"status": "error", "message": "Не вдалося зберегти чат у базі даних"}
            return {"status": "ok", "message": "Чат додано до розсилки", "chat": saved}
        except errors.FloodWaitError as exc:
            return {"status": "error", "message": f"Telegram просить зачекати {exc.seconds} с перед вступом.", "retry_after": exc.seconds}
        except errors.InviteRequestSentError:
            return {"status": "error", "message": "Заявку на вступ надіслано. Дочекайтеся схвалення адміністратора."}
        except Exception as exc:
            logger.warning("Unable to add discovered chat (%s)", type(exc).__name__)
            return {"status": "error", "message": "Не вдалося приєднатися або додати чат. Перевірте доступ."}
