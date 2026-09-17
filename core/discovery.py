"""One server-owned, cancellable, continuous discovery session."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from config import config
from core.ai_discovery import AIDiscoveryEngine
from core.discovery_relevance import normalize_peer, plan_queries, qualify
from core.finder import TelegramDiscovery
from database.discovery import DiscoveryRepository, DiscoveryStorageError

logger = logging.getLogger("DiscoveryWorker")


class DiscoveryManager:
    def __init__(self, repository: DiscoveryRepository, telegram: Any) -> None:
        self.repo = repository
        self.telegram = telegram
        self.task: asyncio.Task | None = None
        self.search_id: str | None = None
        self.runtime_error: str | None = None
        self._lock = asyncio.Lock()
        self._shutdown = False

    async def restore(self) -> None:
        try:
            await self.repo.preflight()
            session = await self.repo.active()
            if session and session["status"] == "stopping":
                await self.repo.update(session["id"], status="stopped", completed_at=self.now())
            elif session:
                self._launch(session)
        except DiscoveryStorageError as exc:
            self.runtime_error = str(exc)
            logger.warning("Discovery unavailable until database configuration is ready")

    @staticmethod
    def now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _launch(self, session: dict[str, Any]) -> None:
        self.search_id, self.runtime_error = session["id"], None
        self._shutdown = False
        self.task = asyncio.create_task(self._run(session), name=f"discovery-{session['id']}")

    async def start(self, query: str, min_members: int) -> dict[str, Any]:
        async with self._lock:
            await self.repo.preflight()
            if self.task and not self.task.done():
                raise ValueError("Пошук уже триває. Зупиніть його перед новим запуском.")
            if await self.repo.active():
                raise ValueError("Є активна сесія пошуку. Відкрийте її або перезапустіть сервер для відновлення.")
            transport = TelegramDiscovery(self.telegram, self._wait_status)
            if not await transport.ready():
                raise ValueError("Telegram не підключений або не авторизований.")
            session = await self.repo.create(query, min_members)
            self._launch(session)
            return session

    async def stop(self, search_id: str) -> None:
        async with self._lock:
            try:
                session = await self.repo.session(search_id)
            except DiscoveryStorageError:
                # Even when Supabase is down, Stop must stop Telegram/AI requests.
                if self.search_id == search_id and self.task and not self.task.done():
                    self.task.cancel()
                    await asyncio.gather(self.task, return_exceptions=True)
                raise
            if not session:
                raise ValueError("Сесію не знайдено.")
            if session["status"] in {"stopped", "failed", "completed"}:
                return
            try:
                await self.repo.update(search_id, status="stopping", progress_message="Зупиняємо пошук…")
            finally:
                if self.search_id == search_id and self.task and not self.task.done():
                    self.task.cancel()
                    await asyncio.gather(self.task, return_exceptions=True)
            await self.repo.update(search_id, status="stopped", completed_at=self.now(), next_request_at=None,
                                   progress_message="Пошук зупинено. Результати збережено.")

    async def close(self) -> None:
        self._shutdown = True
        if self.task and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)

    async def _wait_status(self, seconds: int, message: str) -> None:
        await self.repo.update(self.search_id, status="waiting" if seconds else "searching", progress_message=message,
                               next_request_at=(datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat() if seconds else None)

    async def _run(self, session: dict[str, Any]) -> None:
        search_id = session["id"]
        ai = None
        try:
            ai = AIDiscoveryEngine()
            transport = TelegramDiscovery(self.telegram, self._wait_status)
            total = (await self.repo.results(search_id, limit=1, decision="all", include_hidden=True))["total"]
            relevant = (await self.repo.results(search_id, limit=1))["total"]
            cycle, queries_run = session.get("cycle", 0), session.get("queries_run", 0)
            # Honour a persisted FloodWait/cycle pause even after a process restart.
            if session.get("next_request_at"):
                remaining = (datetime.fromisoformat(session["next_request_at"]) - datetime.now(timezone.utc)).total_seconds()
                await asyncio.sleep(max(0, remaining))
            queries = await self.repo.queries(search_id)
            if not queries:
                queries = plan_queries(session["query"])
                await self.repo.save_queries(search_id, queries)
            while True:
                if not await transport.ready():
                    await self._wait_status(60, "Telegram не підключений або не авторизований. Очікуємо відновлення.")
                    await asyncio.sleep(60)
                    continue
                cycle += 1
                await self.repo.update(search_id, status="searching", cycle=cycle, next_request_at=None,
                                       progress_message="Розширюємо пошукові слова…")
                if len(queries) < 120:
                    additions = await ai.expand(session["query"], queries)
                    expanded = plan_queries(session["query"], [*queries, *additions])
                    fresh = [q for q in expanded if q not in queries]
                    await self.repo.save_queries(search_id, fresh)
                    queries = expanded
                excluded = await self.repo.existing_peers()
                # Resume groups saved just before a shutdown, even if the query cursor advanced.
                while pending := await self.repo.pending(search_id):
                    for candidate in pending:
                        candidate = await transport.enrich(candidate)
                        result = await ai.assess(candidate, session["min_members"])
                        await self.repo.update_candidate(search_id, result)
                        relevant += int(result["decision"] == "accepted")
                for query in queries:
                    await self.repo.update(search_id, status="searching", current_query=query,
                                           ai_status=ai.status, next_request_at=None, progress_message="Шукаємо нові групи…")
                    cursor = await self.repo.cursor(search_id, query)
                    async for candidates, next_cursor in transport.search(query, cursor):
                        to_assess = []
                        for candidate in candidates:
                            if await self.repo.known(candidate["telegram_id"], candidate["peer"]):
                                continue
                            if normalize_peer(candidate["peer"]) in excluded or str(candidate["telegram_id"]) in excluded:
                                result = qualify(candidate)
                                result.update(decision="existing", ai_reason="Уже є у списку розсилки.")
                            else:
                                result = qualify(candidate, session["min_members"])
                                result.update(decision="unverified", assessed_by="pending", ai_reason="Очікує перевірки контенту.")
                            if await self.repo.save_candidate(search_id, result):
                                total += 1
                                if result["assessed_by"] == "pending":
                                    to_assess.append(candidate)
                        # Save the entire discovered page before slow Telegram/AI enrichment.
                        for candidate in to_assess:
                            await self.repo.update(search_id, status="scoring", progress_message=f"Перевіряємо: {candidate['title'][:100]}")
                            candidate = await transport.enrich(candidate)
                            result = await ai.assess(candidate, session["min_members"])
                            await self.repo.update_candidate(search_id, result)
                            relevant += int(result["decision"] == "accepted")
                            await self.repo.update(search_id, total_candidates=total, total_relevant=relevant, ai_status=ai.status)
                        await self.repo.update(search_id, total_candidates=total, total_relevant=relevant)
                        if next_cursor is not None:
                            await self.repo.save_cursor(search_id, query, next_cursor)
                    queries_run += 1
                    await self.repo.update(search_id, queries_run=queries_run)
                await self._wait_status(config.DISCOVERY_CYCLE_SECONDS, "Цикл завершено. Очікуємо перед пошуком нових груп.")
                await asyncio.sleep(config.DISCOVERY_CYCLE_SECONDS)
        except asyncio.CancelledError:
            if not self._shutdown:
                try:
                    await self.repo.update(search_id, status="stopped", completed_at=self.now(),
                                           next_request_at=None, progress_message="Пошук зупинено. Результати збережено.")
                except DiscoveryStorageError as exc:
                    self.runtime_error = str(exc)
            raise
        except Exception as exc:
            self.runtime_error = str(exc) if isinstance(exc, DiscoveryStorageError) else f"Пошук перервано ({type(exc).__name__}). Перевірте підключення Telegram."
            logger.error("Discovery stopped: %s", type(exc).__name__)
            try:
                await self.repo.update(search_id, status="failed", error_message=self.runtime_error,
                                       completed_at=self.now(), next_request_at=None)
            except DiscoveryStorageError:
                logger.error("Unable to persist discovery failure; retained in server state")
        finally:
            if ai:
                await ai.close()
