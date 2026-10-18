import asyncio
import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

telegram_module = sys.modules.setdefault("core.client", ModuleType("core.client"))
telegram_module.client = getattr(telegram_module, "client", MagicMock())
telegram_module.get_latest_saved_message = getattr(telegram_module, "get_latest_saved_message", AsyncMock())

import httpx
from supabase import ClientOptions, create_client
from telethon import errors
from telethon.tl.types import Channel, ChatPhotoEmpty

from core.ai_discovery import AIDiscoveryEngine, Assessment
from core.discovery import DiscoveryManager
from core.discovery_relevance import (channel_is_topical, detect_language, extract_usernames, qualify, rank)
from core.finder import FloodDeferred, TelegramDiscovery
from database.discovery import DiscoveryRepository, SessionStopped
from tests.test_discovery import FakeAI, FakeTransport, MemoryRepository

NOW = datetime.now(timezone.utc)


def spanish_group(**changes):
    value = dict(telegram_id=-2001, peer="@agencias_of", type="group", title="Agencias OnlyFans España",
                 description="Grupo para agencias y chatters de OnlyFans",
                 messages=["Busco chatters para agencia OnlyFans", "Vendo cuentas de Reddit para modelos OnlyFans, DM @seller_es",
                           "¿Alguien con tráfico de Reddit para agencia OnlyFans?", "Promoción OnlyFans en Reddit t.me/promo_es"],
                 participants_count=3400, posting_access="join_required", message_senders=4,
                 last_message_at=(NOW - timedelta(hours=1)).isoformat())
    return {**value, **changes}


def channel(tid=777, username="of_agency_news", title="OF Agency news", left=True, megagroup=False):
    return Channel(id=tid, title=title, photo=ChatPhotoEmpty(), date=NOW, username=username,
                   left=left, megagroup=megagroup, access_hash=1)


class QualityTests(unittest.TestCase):
    def test_tier1_language_group_qualifies_and_is_ranked(self):
        result = qualify(spanish_group(), 1000)
        self.assertEqual(result["decision"], "accepted")
        self.assertEqual(result["language"], "es")
        self.assertEqual(result["ad_policy"], "likely_allowed")
        self.assertGreater(result["final_score"], qualify(spanish_group(participants_count=50))["final_score"])

    def test_targets_must_be_writable_and_alive(self):
        self.assertEqual(qualify(spanish_group(posting_access="restricted"))["decision"], "not_postable")
        old = (NOW - timedelta(days=45)).isoformat()
        self.assertEqual(qualify(spanish_group(last_message_at=old))["decision"], "inactive")

    def test_topical_channels_are_kept_apart_from_noise(self):
        for title in ["ELEPHANT | OF AGENCY", "Reddit Accounts for Sale", "ВЕБКАМ РАБОТА СТУДИЯ"]:
            with self.subTest(title=title):
                row = dict(telegram_id=1, peer="@c", type="channel", title=title, messages=[])
                self.assertEqual(qualify(row)["decision"], "channel")
        for title in ["Stripchat Tokens Buy", "OnlyFans leaks 18+", "Reddit memes"]:
            with self.subTest(title=title):
                self.assertFalse(channel_is_topical(dict(title=title)))

    def test_leads_skip_bots_and_deduplicate(self):
        names = extract_usernames(["join @OFM_chat and t.me/ofm_chat, ask @helper_bot", "https://t.me/+invite", "mail@example.com"])
        self.assertEqual([n.lower() for n in names], ["ofm_chat"])

    def test_language_detection(self):
        self.assertEqual(detect_language(["Wir suchen eine OnlyFans Agentur für die Zusammenarbeit und nicht nur das"]), "de")
        self.assertEqual(detect_language(["Шукаю агенцію для моделей, потрібні чатери і трафік, пишіть"]), "uk")
        self.assertIsNone(detect_language(["hi"]))

    def test_rank_prefers_reach_and_ad_tolerance(self):
        self.assertGreater(rank(80, 20000, 85, "likely_allowed"), rank(80, 200, 85, "unknown"))


class AIGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_ai_accept_still_requires_posting_rights(self):
        engine = object.__new__(AIDiscoveryEngine)
        group = spanish_group(title="Grupo general", description="", posting_access="restricted",
                              messages=["Busco chatters OnlyFans agencia", "Agencia OnlyFans busca tráfico Reddit"])
        engine._json = AsyncMock(return_value=Assessment(professional_audience=True, adult_business=True, relevance=90,
                                                          reason="OFM", evidence=["Busco chatters OnlyFans agencia"], language="es"))
        result = await engine.assess(group, 0)
        self.assertEqual(result["decision"], "not_postable")
        self.assertEqual(result["language"], "es")


class SourcesTransport(FakeTransport):
    """A search that returns one topical channel and one relevant group."""
    linked = None
    resolved: dict = {}
    defer = False

    async def search(self, query, cursor):
        yield [TelegramDiscovery.parse(channel(), query),
               {**spanish_group(), "matched_query": query, "source": "search", "_forward_chats": [channel(888, "forwarded_ofm", "OFM agency hub")]}], {}

    async def expand_channel(self, row):
        group = TelegramDiscovery.parse(channel(999, "of_agency_chat", "OF Agency chat", megagroup=True), "", "linked", row["peer"])
        row["linked_peer"] = "@of_agency_chat"
        return [group]

    async def resolve(self, username, source, source_peer):
        if SourcesTransport.defer:
            raise FloodDeferred(3600)
        row = TelegramDiscovery.parse(channel(5000 + len(username), username, f"{username} OFM agency", megagroup=True),
                                      f"@{username}", source, source_peer)
        SourcesTransport.resolved[username] = row
        return row


class WorkerSourcesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.repo = MemoryRepository()
        self.manager = DiscoveryManager(self.repo, object())
        SourcesTransport.resolved, SourcesTransport.defer = {}, False
        self.patches = [patch("core.discovery.AIDiscoveryEngine", FakeAI), patch("core.discovery.TelegramDiscovery", SourcesTransport),
                        patch("core.discovery.plan_queries", return_value=["OFM"])]
        for p in self.patches:
            p.start()

    async def asyncTearDown(self):
        await self.manager.close()
        for p in self.patches:
            p.stop()

    async def run_one_cycle(self):
        session = await self.manager.start("OFM", 0)
        for _ in range(200):
            await asyncio.sleep(0)
            if self.repo.sessions[session["id"]].get("status") == "waiting":
                break
        await self.manager.stop(session["id"])
        return {row["peer"]: row for row in self.repo.rows.values()}

    async def test_all_sources_feed_the_same_session(self):
        rows = await self.run_one_cycle()
        self.assertEqual(rows["@of_agency_news"]["decision"], "channel")
        self.assertEqual(rows["@of_agency_news"]["linked_peer"], "@of_agency_chat")
        self.assertEqual(rows["@of_agency_chat"]["source"], "linked")
        self.assertEqual(rows["@forwarded_ofm"]["source"], "forward")
        self.assertEqual(rows["@forwarded_ofm"]["source_peer"], "@agencias_of")
        # @mentions in a relevant group become leads and are resolved between queries.
        self.assertIn("seller_es", SourcesTransport.resolved)
        self.assertEqual(rows["@seller_es"]["source"], "mention")
        self.assertEqual(self.repo.leads["promo_es"]["status"], "done")

    async def test_rate_limited_leads_do_not_stall_search(self):
        SourcesTransport.defer = True
        rows = await self.run_one_cycle()
        self.assertIn("@agencias_of", rows)
        self.assertEqual(self.repo.leads["seller_es"]["status"], "pending")  # kept for later
        self.assertNotIn("@seller_es", rows)

    async def test_productive_queries_first_and_empty_ones_rested(self):
        stats = {"a": {"found": 3, "accepted": 0, "empty": 0}, "b": {"found": 9, "accepted": 2, "empty": 0},
                 "c": {"found": 0, "accepted": 0, "empty": 2}, "d": {"found": 0, "accepted": 0, "empty": 0}}
        self.assertEqual(DiscoveryManager._query_order(stats), ["b", "a", "d"])
        for item in stats.values():
            item["empty"] = 2
        self.assertEqual(len(DiscoveryManager._query_order(stats)), 4)  # all exhausted: start over


class LeaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.repo = MemoryRepository()
        self.patches = [patch("core.discovery.AIDiscoveryEngine", FakeAI), patch("core.discovery.TelegramDiscovery", FakeTransport),
                        patch("core.discovery.plan_queries", return_value=["OFM"])]
        for p in self.patches:
            p.start()

    async def asyncTearDown(self):
        for p in self.patches:
            p.stop()

    async def test_second_process_does_not_resume_a_running_session(self):
        server, local = DiscoveryManager(self.repo, object()), DiscoveryManager(self.repo, object())
        session = await server.start("OFM", 0)
        await local.restore()
        self.assertIsNone(local.task)
        await server.stop(session["id"])
        await server.close()
        await local.close()

    async def test_stop_pressed_in_another_process_stops_the_worker(self):
        server, web = DiscoveryManager(self.repo, object()), DiscoveryManager(self.repo, object())
        with patch("core.discovery.HEARTBEAT_SECONDS", 0.01):
            session = await server.start("OFM", 0)
            await web.stop(session["id"])  # no local task in this process
            await asyncio.wait_for(asyncio.gather(server.task, return_exceptions=True), 2)
        self.assertTrue(server.task.done())
        self.assertEqual(self.repo.sessions[session["id"]]["status"], "stopped")
        self.assertNotIn(session["id"], self.repo.owner)  # released
        await server.close()


class TransportV2Tests(unittest.IsolatedAsyncioTestCase):
    async def test_long_flood_on_optional_request_is_deferred(self):
        transport = TelegramDiscovery(object(), AsyncMock())
        with self.assertRaises(FloodDeferred):
            await transport.call(AsyncMock(side_effect=errors.FloodWaitError(request=None, capture=3600)), max_flood=120)

    async def test_channel_expands_to_discussion_group_and_similar(self):
        linked = channel(999, "of_agency_chat", "OF Agency chat", megagroup=True)
        similar = channel(1001, "ofm_insider", "OFM insider")
        full = SimpleNamespace(full_chat=SimpleNamespace(about="OnlyFans agency news", participants_count=5000, linked_chat_id=999),
                               chats=[linked])
        client = AsyncMock(side_effect=[full, SimpleNamespace(chats=[similar])])
        transport = TelegramDiscovery(client, AsyncMock())
        transport._next_call = 0
        row = TelegramDiscovery.parse(channel(), "OFM")
        with patch("core.finder.asyncio.sleep", new=AsyncMock()):
            found = await transport.expand_channel(row)
        self.assertEqual(row["linked_peer"], "@of_agency_chat")
        self.assertEqual(row["participants_count"], 5000)
        self.assertEqual([(r["peer"], r["source"], r["type"]) for r in found],
                         [("@of_agency_chat", "linked", "group"), ("@ofm_insider", "similar", "channel")])


class StorageV2Tests(unittest.IsolatedAsyncioTestCase):
    def repo(self, handler):
        http = httpx.Client(transport=httpx.MockTransport(handler))
        self.addCleanup(http.close)
        return DiscoveryRepository(create_client("https://example.supabase.co", "test-key", options=ClientOptions(httpx_client=http)))

    async def test_known_chats_never_become_leads(self):
        upserts = []

        def handle(request):
            if request.url.path.endswith("discovery_results"):
                return httpx.Response(200, json=[{"telegram_peer": "@known_chat"}])
            upserts.append(json.loads(request.content))
            return httpx.Response(201, json=upserts[-1])

        added = await self.repo(handle).add_leads("s1", [{"username": "Known_Chat"}, {"username": "New_Chat", "source_peer": "@g"}])
        self.assertEqual(added, 1)
        self.assertEqual([row["username_key"] for row in upserts[0]], ["new_chat"])

    async def test_session_claim_is_conditional(self):
        seen = []

        def handle(request):
            seen.append(request)
            return httpx.Response(200, json=[])

        self.assertFalse(await self.repo(handle).claim("s1", "me", 90))
        self.assertIn("worker_id.eq.me", seen[0].url.params["or"])

    async def test_progress_write_never_resurrects_a_stopped_session(self):
        seen = []

        def handle(request):
            seen.append(request)
            return httpx.Response(200, json=[])  # the session is no longer running

        with self.assertRaises(SessionStopped):
            await self.repo(handle).update("s1", running_only=True, status="searching")
        self.assertEqual(seen[0].url.params["status"], "in.(pending,searching,scoring,waiting)")

    async def test_best_results_first(self):
        seen = []

        def handle(request):
            seen.append(request)
            return httpx.Response(200, json=[], headers={"content-range": "0-0/0"})

        await self.repo(handle).results("s1", decision="accepted", sort="score", language="es")
        self.assertTrue(seen[0].url.params["order"].startswith("final_score.desc"))
        self.assertEqual(seen[0].url.params["language"], "eq.es")


if __name__ == "__main__":
    unittest.main()
