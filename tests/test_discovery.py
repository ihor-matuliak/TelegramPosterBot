import asyncio
import copy
import sys
import threading
import unittest
from types import SimpleNamespace, ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

# No real Telegram session or account is opened by these tests.
telegram_module = ModuleType("core.client")
telegram_module.client = MagicMock()
telegram_module.get_latest_saved_message = AsyncMock()
sys.modules.setdefault("core.client", telegram_module)

from core.discovery import DiscoveryManager
from core.discovery_relevance import plan_queries, qualify
from core.ai_discovery import AIDiscoveryEngine, Assessment
from core.finder import TelegramDiscovery
from database.discovery import DiscoveryRepository, DiscoveryStorageError
from telethon import errors
from telethon.tl.types import Channel


def candidate(**changes):
    value = dict(telegram_id=-100123, peer="@agency", title="OnlyFans Reddit Agency",
                 description="OFM agency: Reddit traffic suppliers. Advertising allowed.",
                 messages=["Need Reddit accounts for OnlyFans agency traffic", "OnlyFans promotion services for agencies"],
                 type="group", participants_count=1200, can_post=False, posting_access="join_required")
    return {**value, **changes}


class QualificationTests(unittest.TestCase):
    def test_professional_reddit_group_qualifies(self):
        result = qualify(candidate(), 1000)
        self.assertEqual(result["decision"], "accepted")
        self.assertEqual(result["topic"], "reddit_traffic")
        self.assertEqual(result["ad_policy"], "allowed")
        self.assertFalse(result["can_post"])

    def test_unrelated_groups_rejected(self):
        for title in ["Reddit memes", "Football fans", "Road traffic", "OnlyFans leaks"]:
            with self.subTest(title=title):
                self.assertEqual(qualify(candidate(title=title, description="", messages=["Hi", "Hello"]))["decision"], "rejected")

    def test_one_stray_ad_does_not_define_group(self):
        result = qualify(candidate(title="General discussion", description="", messages=["OnlyFans agency Reddit traffic", "Hello", "Weather today"]))
        self.assertEqual(result["decision"], "rejected")

    def test_professional_name_with_unrelated_content_rejected(self):
        result = qualify(candidate(messages=["funny cats", "football highlights"]))
        self.assertEqual(result["decision"], "rejected")

    def test_repeated_spam_is_not_independent_evidence(self):
        result = qualify(candidate(title="General group", description="", messages=["OnlyFans agency traffic"] * 5))
        self.assertEqual(result["decision"], "rejected")

    def test_no_content_is_unverified(self):
        self.assertEqual(qualify(candidate(messages=[]))["decision"], "unverified")

    def test_minimum_and_unknown_count(self):
        self.assertEqual(qualify(candidate(participants_count=99), 100)["decision"], "below_minimum")
        self.assertEqual(qualify(candidate(participants_count=None), 100)["decision"], "unverified")
        self.assertEqual(qualify(candidate(participants_count=100), 100)["decision"], "accepted")

    def test_ads_and_relevance_are_separate(self):
        result = qualify(candidate(description="OnlyFans agency. No ads."))
        self.assertEqual(result["decision"], "accepted")
        self.assertEqual(result["ad_policy"], "prohibited")

    def test_query_expansion_is_bounded_and_deduplicated(self):
        queries = plan_queries("OnlyFans, onlyfans, OF, Fans, Reddit", ["OFM"] * 50)
        self.assertEqual(len(queries), len({q.lower() for q in queries}))
        self.assertNotIn("OF", queries)
        self.assertIn("Reddit OnlyFans", queries)
        self.assertIn("CamSoda", queries)


class AIQualificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_hallucinated_evidence_cannot_qualify(self):
        engine = object.__new__(AIDiscoveryEngine)
        engine._json = AsyncMock(return_value=Assessment(professional_audience=True, adult_business=True,
            relevance=99, reason="Relevant", evidence=["This quote does not exist in the group"]))
        result = await engine.assess(candidate(title="Reddit memes", description="", messages=["funny cat", "funny dog"]), 0)
        self.assertEqual(result["decision"], "rejected")

    async def test_ai_failure_preserves_honest_rule_result(self):
        engine = object.__new__(AIDiscoveryEngine)
        engine._json = AsyncMock(return_value=None)
        result = await engine.assess(candidate(), 0)
        self.assertEqual(result["assessed_by"], "rules")
        self.assertEqual(result["decision"], "accepted")


class MemoryRepository:
    def __init__(self):
        self.sessions, self.rows, self.query_rows, self.cursors = {}, {}, {}, {}
        self.saved = asyncio.Event()

    async def preflight(self): pass
    async def active(self):
        return next((s for s in self.sessions.values() if s["status"] in ["pending", "searching", "scoring", "waiting", "stopping"]), None)
    async def create(self, query, min_members):
        session = {"id": str(len(self.sessions) + 1), "query": query, "min_members": min_members, "status": "pending"}
        self.sessions[session["id"]] = session
        return dict(session)
    async def session(self, sid): return self.sessions.get(sid)
    async def update(self, sid, **fields): self.sessions[sid].update(fields)
    async def queries(self, sid): return self.query_rows.get(sid, [])
    async def save_queries(self, sid, queries): self.query_rows.setdefault(sid, []).extend(queries)
    async def existing_peers(self): return set()
    async def known(self, tid, peer): return tid in self.rows
    async def cursor(self, sid, query): return self.cursors.get((sid, query), {})
    async def save_cursor(self, sid, query, cursor): self.cursors[(sid, query)] = cursor
    async def save_candidate(self, sid, row):
        if row["telegram_id"] in self.rows: return False
        self.rows[row["telegram_id"]] = {**row, "search_id": sid}
        self.saved.set()
        return True
    async def update_candidate(self, sid, row):
        self.rows[row["telegram_id"]] = {**row, "search_id": sid}
    async def pending(self, sid):
        return [copy.deepcopy(r) for r in self.rows.values() if r["search_id"] == sid and r.get("assessed_by") == "pending"]
    async def results(self, sid=None, offset=0, limit=50, decision="accepted", include_hidden=False):
        rows = [r for r in self.rows.values() if (sid is None or r["search_id"] == sid) and (decision == "all" or r["decision"] == decision)]
        return {"results": rows[offset:offset+limit], "total": len(rows)}


class FakeAI:
    status = "disabled"
    async def expand(self, query, previous): return []
    async def assess(self, row, minimum): return qualify(row, minimum)
    async def close(self): pass


class FakeTransport:
    def __init__(self, client, on_wait): pass
    async def ready(self): return True
    async def enrich(self, row): return row
    async def search(self, query, cursor):
        yield [candidate(), candidate(peer="@renamed_agency")], {"id": 42}


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.repo = MemoryRepository()
        self.manager = DiscoveryManager(self.repo, object())
        self.patches = [patch("core.discovery.AIDiscoveryEngine", FakeAI), patch("core.discovery.TelegramDiscovery", FakeTransport), patch("core.discovery.plan_queries", return_value=["OFM"])]
        for p in self.patches: p.start()
    async def asyncTearDown(self):
        await self.manager.close()
        for p in self.patches: p.stop()

    async def test_unique_across_queries_renames_and_sessions(self):
        first = await self.manager.start("OFM", 100)
        await asyncio.wait_for(self.repo.saved.wait(), 2)
        await self.manager.stop(first["id"])
        second = await self.manager.start("OnlyFans", 100)
        for _ in range(20): await asyncio.sleep(0)
        await self.manager.stop(second["id"])
        self.assertEqual(len(self.repo.rows), 1)
        self.assertEqual((await self.repo.results(second["id"]))["total"], 0)

    async def test_stop_immediately_after_start(self):
        session = await self.manager.start("OFM", 0)
        await self.manager.stop(session["id"])
        self.assertEqual(self.repo.sessions[session["id"]]["status"], "stopped")
        self.assertFalse(self.repo.rows)

    async def test_concurrent_start_rejected(self):
        await self.manager.start("OFM", 0)
        with self.assertRaises(ValueError): await self.manager.start("OFM", 0)

    async def test_db_failure_is_not_empty_success(self):
        self.repo.results = AsyncMock(side_effect=DiscoveryStorageError("DB unavailable"))
        session = await self.manager.start("OFM", 0)
        await self.manager.task
        self.assertEqual(self.repo.sessions[session["id"]]["status"], "failed")
        self.assertIn("DB unavailable", self.manager.runtime_error)

    async def test_resume_and_stop_during_wait(self):
        session = await self.repo.create("OFM", 0)
        await self.manager.restore()
        await asyncio.wait_for(self.repo.saved.wait(), 2)
        await self.manager.stop(session["id"])
        self.assertEqual(self.repo.sessions[session["id"]]["status"], "stopped")

    async def test_discovery_is_saved_before_slow_enrichment(self):
        processing = asyncio.Event()
        async def slow_enrich(transport, row):
            processing.set()
            await asyncio.Event().wait()
        with patch.object(FakeTransport, "enrich", slow_enrich):
            session = await self.manager.start("OFM", 0)
            await asyncio.wait_for(processing.wait(), 2)
            await self.manager.stop(session["id"])
        self.assertEqual(len(self.repo.rows), 1)
        self.assertEqual(next(iter(self.repo.rows.values()))["assessed_by"], "pending")

    async def test_restart_finishes_pending_content_check(self):
        session = await self.repo.create("OFM", 0)
        row = qualify(candidate())
        row.update(assessed_by="pending", decision="unverified")
        await self.repo.save_candidate(session["id"], row)
        await self.manager.restore()
        for _ in range(30): await asyncio.sleep(0)
        await self.manager.stop(session["id"])
        self.assertEqual(next(iter(self.repo.rows.values()))["decision"], "accepted")

    async def test_stop_still_cancels_work_when_db_read_fails(self):
        session = await self.manager.start("OFM", 0)
        self.repo.session = AsyncMock(side_effect=DiscoveryStorageError("DB down"))
        with self.assertRaises(DiscoveryStorageError): await self.manager.stop(session["id"])
        self.assertTrue(self.manager.task.done())


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_stop_interrupts_flood_wait(self):
        waiting = asyncio.Event()
        async def notify(seconds, message):
            self.assertGreaterEqual(seconds, 120)
            waiting.set()
        transport = TelegramDiscovery(object(), notify)
        op = AsyncMock(side_effect=errors.FloodWaitError(request=None, capture=120))
        task = asyncio.create_task(transport.call(op))
        await asyncio.wait_for(waiting.wait(), 2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError): await task
        self.assertEqual(op.await_count, 1)

    async def test_unknown_participants_remain_unknown(self):
        chat = Channel(id=42, title="OFM", photo=None, date=None, megagroup=True, username="ofmgroup", left=True)
        row = TelegramDiscovery.parse(chat, "OFM")
        self.assertIsNone(row["participants_count"])
        self.assertEqual(row["posting_access"], "join_required")

    async def test_db_write_finishes_before_cancel_returns(self):
        started, finish = threading.Event(), threading.Event()
        def write():
            started.set()
            finish.wait(2)
            return "saved"
        repository = DiscoveryRepository(object())
        task = asyncio.create_task(repository.execute(write))
        await asyncio.to_thread(started.wait, 2)
        task.cancel()
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        finish.set()
        with self.assertRaises(asyncio.CancelledError): await task


if __name__ == "__main__":
    unittest.main()
