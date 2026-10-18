"""One server-owned, cancellable, continuous discovery session.

Sources, in the order they are worked through:
  * search queries (user seeds, curated multilingual vocabulary, AI expansion), most productive first;
  * for topical channels: their discussion group and Telegram's "similar channels";
  * snowball from relevant groups: channels forwarded into them and @mentions / t.me links in them.
"""

import asyncio
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from telethon import errors

from config import config
from core.ai_discovery import AIDiscoveryEngine
from core.discovery_relevance import channel_is_topical, extract_usernames, normalize_peer, plan_queries, qualify
from core.finder import FloodDeferred, TelegramDiscovery
from database.discovery import ACTIVE_STATUSES, DiscoveryRepository, DiscoveryStorageError, SessionStopped

logger = logging.getLogger("DiscoveryWorker")

MAX_QUERIES = 200
EMPTY_CYCLES_SKIP = 2          # a query that found nothing new twice in a row is skipped
LEADS_PER_QUERY = 3            # snowball leads resolved between two search queries
LEADS_PER_CYCLE = 40           # ResolveUsername is strictly rate limited by Telegram
EXPANSIONS_PER_CYCLE = 60      # channels expanded into discussion groups / similar channels
LEASE_TTL_SECONDS = 90
HEARTBEAT_SECONDS = 20
# Decisions that prove the chat is on topic, so whatever it links to is worth a look.
TOPICAL = {"accepted", "not_postable", "inactive", "channel"}


class DiscoveryManager:
    def __init__(self, repository: DiscoveryRepository, telegram: Any) -> None:
        self.repo = repository
        self.telegram = telegram
        self.task: asyncio.Task | None = None
        self.search_id: str | None = None
        self.runtime_error: str | None = None
        self.instance_id = uuid.uuid4().hex
        self._heartbeat: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._shutdown = False
        self._lease_lost = False
        self._total = 0
        self._relevant = 0
        self._budget: dict[str, int] = {}
        self._excluded: set[str] = set()
        self._leads_paused_until = 0.0
        self._adopt: asyncio.Task | None = None

    async def restore(self) -> None:
        try:
            await self.repo.preflight()
            session = await self.repo.active()
            if session and session["status"] == "stopping":
                await self.repo.update(session["id"], status="stopped", completed_at=self.now())
            elif session:
                if await self.repo.claim(session["id"], self.instance_id, LEASE_TTL_SECONDS):
                    self._launch(session)
                else:
                    logger.info("Discovery session %s runs in another process; not resuming here.", session["id"])
                    self._adopt = asyncio.create_task(self._adopt_when_free(session["id"]))
        except DiscoveryStorageError as exc:
            self.runtime_error = str(exc)
            logger.warning("Discovery unavailable until database configuration is ready")

    @staticmethod
    def now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _launch(self, session: dict[str, Any]) -> None:
        self.search_id, self.runtime_error = session["id"], None
        self._shutdown = self._lease_lost = False
        self.task = asyncio.create_task(self._run(session), name=f"discovery-{session['id']}")
        self._heartbeat = asyncio.create_task(self._keep_lease(session["id"], self.task),
                                              name=f"discovery-lease-{session['id']}")

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
            await self.repo.claim(session["id"], self.instance_id, LEASE_TTL_SECONDS)
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
                # A session running in another process stops itself when its heartbeat sees this.
                await self.repo.update(search_id, status="stopping", progress_message="Зупиняємо пошук…")
            finally:
                if self.search_id == search_id and self.task and not self.task.done():
                    self.task.cancel()
                    await asyncio.gather(self.task, return_exceptions=True)
            await self.repo.update(search_id, status="stopped", completed_at=self.now(), next_request_at=None,
                                   progress_message="Пошук зупинено. Результати збережено.")

    async def close(self) -> None:
        self._shutdown = True
        for task in (self._adopt, self.task, self._heartbeat):
            if task and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def _adopt_when_free(self, search_id: str) -> None:
        """Resume a session whose owner died without releasing it, once its lease has expired."""
        while True:
            await asyncio.sleep(LEASE_TTL_SECONDS / 3)
            try:
                session = await self.repo.session(search_id)
                if not session or session["status"] not in ACTIVE_STATUSES or session["status"] == "stopping":
                    return
                async with self._lock:
                    if self.task and not self.task.done():
                        return
                    if await self.repo.claim(search_id, self.instance_id, LEASE_TTL_SECONDS):
                        logger.info("Resuming discovery session %s abandoned by another process.", search_id)
                        self._launch(session)
                        return
            except DiscoveryStorageError:
                continue

    async def _keep_lease(self, search_id: str, run: asyncio.Task) -> None:
        """Renew the session lease; stop locally on Stop from another process or when the lease is lost."""
        renewed = time.monotonic()
        while not run.done():
            await asyncio.sleep(HEARTBEAT_SECONDS)
            try:
                session = await self.repo.session(search_id)
                if not session or session["status"] not in ACTIVE_STATUSES or session["status"] == "stopping":
                    run.cancel()
                    return
                if not await self.repo.claim(search_id, self.instance_id, LEASE_TTL_SECONDS):
                    logger.error("Discovery session %s was taken over by another process; stopping here.", search_id)
                    self._lease_lost = True
                    run.cancel()
                    return
                renewed = time.monotonic()
            except DiscoveryStorageError:
                if time.monotonic() - renewed > LEASE_TTL_SECONDS / 2:
                    logger.error("Cannot renew discovery lease; stopping before another process may take over.")
                    self._lease_lost = True
                    run.cancel()
                    return

    async def _wait_status(self, seconds: int, message: str) -> None:
        await self.repo.update(self.search_id, running_only=True, status="waiting" if seconds else "searching", progress_message=message,
                               next_request_at=(datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat() if seconds else None)

    @staticmethod
    def _query_order(stats: dict[str, dict[str, int]]) -> list[str]:
        """Productive queries first; queries that keep finding nothing new are rested."""
        active = [q for q, s in stats.items() if s["empty"] < EMPTY_CYCLES_SKIP]
        if not active:
            for s in stats.values():
                s["empty"] = 0
            active = list(stats)
        position = {q: i for i, q in enumerate(stats)}
        return sorted(active, key=lambda q: (-stats[q]["accepted"], -stats[q]["found"], position[q]))

    async def _counters(self, search_id: str, ai: Any) -> None:
        await self.repo.update(search_id, running_only=True, total_candidates=self._total, total_relevant=self._relevant, ai_status=ai.status)

    async def _ingest(self, search_id: str, session: dict[str, Any], transport: Any, ai: Any,
                      candidates: list[dict[str, Any]]) -> tuple[int, int]:
        """Save never-seen chats at once, then check their content. Returns (new, accepted)."""
        found, accepted, to_assess = 0, 0, []
        for candidate in candidates:
            if await self.repo.known(candidate["telegram_id"], candidate["peer"]):
                continue
            if normalize_peer(candidate["peer"]) in self._excluded or str(candidate["telegram_id"]) in self._excluded:
                result = qualify(candidate)
                result.update(decision="existing", ai_reason="Уже є у списку розсилки.")
            else:
                result = qualify(candidate, session["min_members"])
                result.update(decision="unverified", assessed_by="pending", ai_reason="Очікує перевірки контенту.")
            if await self.repo.save_candidate(search_id, result):
                found += 1
                self._total += 1
                if result["assessed_by"] == "pending":
                    to_assess.append(candidate)
        # The whole page is saved before slow Telegram/AI enrichment.
        for candidate in to_assess:
            accepted += await self._assess(search_id, session, transport, ai, candidate)
        if found:
            await self._counters(search_id, ai)
        return found, accepted

    async def _assess(self, search_id: str, session: dict[str, Any], transport: Any, ai: Any,
                      candidate: dict[str, Any]) -> int:
        await self.repo.update(search_id, running_only=True, status="scoring", progress_message=f"Перевіряємо: {candidate['title'][:100]}")
        candidate = await transport.enrich(candidate)
        extra: list[dict[str, Any]] = []
        if candidate.get("type") != "group" and channel_is_topical(candidate) and self._budget.get("expand", 0) > 0:
            self._budget["expand"] -= 1
            extra.extend(await transport.expand_channel(candidate))
        result = await ai.assess(candidate, session["min_members"])
        await self.repo.update_candidate(search_id, result)
        accepted = int(result["decision"] == "accepted")
        self._relevant += accepted
        await self._counters(search_id, ai)
        if result["decision"] in TOPICAL or result.get("topic") in {"ofm_business", "reddit_traffic"}:
            peer = candidate["peer"]
            extra.extend(row for chat in candidate.get("_forward_chats", [])
                         if (row := transport.parse(chat, peer, "forward", peer)))
            mentions = extract_usernames([candidate.get("description", ""), *candidate.get("messages", [])])
            own = normalize_peer(peer)
            await self.repo.add_leads(search_id, [{"username": name, "source": "mention", "source_peer": peer}
                                                  for name in mentions if name.lower() != own])
        if extra:
            await self._ingest(search_id, session, transport, ai, extra)
        return accepted

    async def _process_leads(self, search_id: str, session: dict[str, Any], transport: Any, ai: Any, limit: int) -> None:
        limit = min(limit, self._budget.get("leads", 0))
        if limit <= 0 or time.monotonic() < self._leads_paused_until:
            return
        for lead in await self.repo.pending_leads(limit):
            self._budget["leads"] -= 1
            try:
                candidate = await transport.resolve(lead["username"], lead.get("source") or "mention", lead.get("source_peer"))
            except FloodDeferred as exc:
                # Keep searching by queries; come back to the leads after Telegram's pause.
                self._leads_paused_until = time.monotonic() + exc.seconds
                logger.info("Lead resolving paused for %ss by Telegram.", exc.seconds)
                return
            except errors.RPCError:
                candidate = None
            await self.repo.finish_lead(lead["id"], "done" if candidate else "failed")
            if candidate:
                await self._ingest(search_id, session, transport, ai, [candidate])

    async def _run(self, session: dict[str, Any]) -> None:
        search_id = session["id"]
        ai = None
        try:
            ai = AIDiscoveryEngine()
            transport = TelegramDiscovery(self.telegram, self._wait_status)
            self._total = (await self.repo.results(search_id, limit=1, decision="all", include_hidden=True))["total"]
            self._relevant = (await self.repo.results(search_id, limit=1))["total"]
            cycle, queries_run = session.get("cycle", 0), session.get("queries_run", 0)
            # Honour a persisted FloodWait/cycle pause even after a process restart.
            if session.get("next_request_at"):
                remaining = (datetime.fromisoformat(session["next_request_at"]) - datetime.now(timezone.utc)).total_seconds()
                await asyncio.sleep(max(0, remaining))
            stats = await self.repo.query_stats(search_id)
            if not stats:
                planned = plan_queries(session["query"])
                await self.repo.save_queries(search_id, planned)
                stats = {q: {"found": 0, "accepted": 0, "empty": 0} for q in planned}
            while True:
                if not await transport.ready():
                    await self._wait_status(60, "Telegram не підключений або не авторизований. Очікуємо відновлення.")
                    await asyncio.sleep(60)
                    continue
                cycle += 1
                self._budget = {"leads": LEADS_PER_CYCLE, "expand": EXPANSIONS_PER_CYCLE}
                await self.repo.update(search_id, running_only=True, status="searching", cycle=cycle, next_request_at=None,
                                       progress_message="Розширюємо пошукові слова…")
                if len(stats) < MAX_QUERIES:
                    best = [q for q, s in sorted(stats.items(), key=lambda i: -i[1]["accepted"]) if s["accepted"]][:15]
                    additions = await ai.expand(session["query"], list(stats), best, await self.repo.accepted_titles(search_id))
                    fresh = [q for q in plan_queries(session["query"], [*stats, *additions]) if q not in stats]
                    fresh = fresh[:MAX_QUERIES - len(stats)]
                    await self.repo.save_queries(search_id, fresh)
                    stats.update({q: {"found": 0, "accepted": 0, "empty": 0} for q in fresh})
                self._excluded = await self.repo.existing_peers()
                # Resume groups saved just before a shutdown, even if the query cursor advanced.
                while pending := await self.repo.pending(search_id):
                    for candidate in pending:
                        await self._assess(search_id, session, transport, ai, candidate)
                for query in self._query_order(stats):
                    await self.repo.update(search_id, running_only=True, status="searching", current_query=query,
                                           ai_status=ai.status, next_request_at=None, progress_message="Шукаємо нові групи…")
                    cursor = await self.repo.cursor(search_id, query)
                    found = accepted = 0
                    async for candidates, next_cursor in transport.search(query, cursor):
                        new, good = await self._ingest(search_id, session, transport, ai, candidates)
                        found, accepted = found + new, accepted + good
                        if next_cursor is not None:
                            await self.repo.save_cursor(search_id, query, next_cursor)
                    item = stats[query]
                    item["found"] += found
                    item["accepted"] += accepted
                    item["empty"] = 0 if found else item["empty"] + 1
                    await self.repo.save_query_stats(search_id, query, item)
                    queries_run += 1
                    await self.repo.update(search_id, running_only=True, queries_run=queries_run,
                                           progress_message="Перевіряємо групи, згадані в знайдених…")
                    await self._process_leads(search_id, session, transport, ai, LEADS_PER_QUERY)
                await self._process_leads(search_id, session, transport, ai, LEADS_PER_CYCLE)
                await self._wait_status(config.DISCOVERY_CYCLE_SECONDS, "Цикл завершено. Очікуємо перед пошуком нових груп.")
                await asyncio.sleep(config.DISCOVERY_CYCLE_SECONDS)
        except SessionStopped:
            logger.info("Discovery session %s was stopped elsewhere; worker exits.", search_id)
        except asyncio.CancelledError:
            if not self._shutdown and not self._lease_lost:
                try:
                    await self.repo.update(search_id, status="stopped", completed_at=self.now(),
                                           next_request_at=None, progress_message="Пошук зупинено. Результати збережено.")
                except DiscoveryStorageError as exc:
                    self.runtime_error = str(exc)
            raise
        except Exception as exc:
            self.runtime_error = str(exc) if isinstance(exc, DiscoveryStorageError) else f"Пошук перервано ({type(exc).__name__}). Перевірте підключення Telegram."
            logger.error("Discovery stopped: %s", type(exc).__name__, exc_info=True)
            try:
                await self.repo.update(search_id, status="failed", error_message=self.runtime_error,
                                       completed_at=self.now(), next_request_at=None)
            except DiscoveryStorageError:
                logger.error("Unable to persist discovery failure; retained in server state")
        finally:
            if ai:
                await ai.close()
            if self._heartbeat and self._heartbeat is not asyncio.current_task() and not self._heartbeat.done():
                self._heartbeat.cancel()
            if not self._lease_lost:
                try:
                    # Lets a restarted or redeployed server pick an unfinished session up at once.
                    await self.repo.release(search_id, self.instance_id)
                except DiscoveryStorageError:
                    pass
