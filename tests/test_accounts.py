import asyncio
import json
import sys
import time
import unittest
from datetime import datetime, timezone
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

# Never open a real Telegram session during import or verification.
telegram = sys.modules.setdefault("core.client", ModuleType("core.client"))
telegram.client = getattr(telegram, "client", MagicMock())
telegram.simulate_typing = getattr(telegram, "simulate_typing", AsyncMock())
telegram.get_latest_saved_message = getattr(telegram, "get_latest_saved_message", AsyncMock())
telegram.init_telegram_client = getattr(telegram, "init_telegram_client", AsyncMock())
telegram.reset_client_session = getattr(telegram, "reset_client_session", AsyncMock())
telegram.logout_client = getattr(telegram, "logout_client", AsyncMock())
telegram.login_with_bot_token = getattr(telegram, "login_with_bot_token", AsyncMock())
telegram.qr_auth = getattr(telegram, "qr_auth", MagicMock())

import httpx
from supabase import ClientOptions, create_client
from telethon import errors
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.types import Channel, ChatPhotoEmpty, InputPeerChannel

from core.accounts import AccountLogin, AccountLoginError, AccountLoginManager, AccountPosterSupervisor
from core.poster import PostOutcome, PosterWorker
from database.client import PRIMARY_ACCOUNT, ChatConflictError, SupabaseDB

ACCOUNT = str(uuid4())


def make_db(handler, ready=True) -> tuple[SupabaseDB, httpx.Client]:
    db = object.__new__(SupabaseDB)
    db._accounts_ready = ready
    db._accounts_checked_at = time.monotonic()  # no schema probing during the test
    http = httpx.Client(transport=httpx.MockTransport(handler))
    db.client = create_client("https://example.supabase.co", "test-key", options=ClientOptions(httpx_client=http))
    return db, http


class AccountScopeStorageTests(unittest.TestCase):
    def test_primary_scope_selects_unassigned_chats_only(self):
        seen = []

        def handle(request):
            seen.append(request)
            return httpx.Response(200, json=[])

        db, http = make_db(handle)
        with http, patch.object(SupabaseDB, "_load_local_chat_posts", return_value={}):
            db.get_due_chats(PRIMARY_ACCOUNT)
            db.get_due_chats(ACCOUNT)
            db.get_due_chats()
        self.assertEqual(seen[0].url.params["account_id"], "is.null")
        self.assertEqual(seen[1].url.params["account_id"], f"eq.{ACCOUNT}")
        self.assertNotIn("account_id", seen[2].url.params)

    def test_secondary_scope_without_migration_returns_nothing(self):
        seen = []
        db, http = make_db(lambda request: seen.append(request) or httpx.Response(200, json=[]), ready=False)
        with http:
            self.assertEqual(db.get_due_chats(ACCOUNT), [])
            self.assertEqual(db.get_hourly_post_count(ACCOUNT), 0)
            self.assertIsNone(db.add_chat("@x", account=ACCOUNT))
        self.assertEqual(seen, [])

    def test_primary_scope_without_migration_keeps_legacy_behaviour(self):
        seen = []
        db, http = make_db(lambda request: seen.append(request) or httpx.Response(200, json=[]), ready=False)
        with http, patch.object(SupabaseDB, "_load_local_chat_posts", return_value={}):
            db.get_due_chats(PRIMARY_ACCOUNT)
        self.assertNotIn("account_id", seen[0].url.params)

    def test_chat_of_another_account_is_never_moved(self):
        calls = []

        def handle(request):
            calls.append((request.method, request))
            if request.method == "GET":
                return httpx.Response(200, json=[{"id": "c1", "account_id": None}])
            return httpx.Response(409, json={"code": "23505", "message": "duplicate key value violates unique constraint"})

        db, http = make_db(handle)
        with http, self.assertRaises(ChatConflictError) as ctx:
            db.add_chat("@shared", 60, account=ACCOUNT)
        self.assertIn("Основний акаунт", str(ctx.exception))
        self.assertNotIn("PATCH", [m for m, _ in calls])
        insert = json.loads(calls[-1][1].content)
        self.assertEqual(insert["account_id"], ACCOUNT)

    def test_existing_chat_of_same_account_is_updated_in_place(self):
        calls = []

        def handle(request):
            calls.append(request)
            if request.method == "GET":
                return httpx.Response(200, json=[{"id": "c1", "account_id": ACCOUNT}])
            return httpx.Response(200, json=[{"id": "c1", "chat_peer": "@own", "account_id": ACCOUNT}])

        db, http = make_db(handle)
        with http:
            saved = db.add_chat("@own", 120, account=ACCOUNT)
        self.assertEqual(saved["id"], "c1")
        self.assertEqual(calls[-1].method, "PATCH")
        self.assertEqual(calls[-1].url.params["id"], "eq.c1")

    def test_logs_of_additional_accounts_are_attributed(self):
        bodies = []

        def handle(request):
            bodies.append(json.loads(request.content))
            return httpx.Response(201, json=[])

        db, http = make_db(handle)
        with http:
            db.add_log("@x", "success", "ok", account=ACCOUNT)
            db.add_log("@x", "success", "ok", account=PRIMARY_ACCOUNT)
        self.assertEqual(bodies[0]["account_id"], ACCOUNT)
        self.assertNotIn("account_id", bodies[1])


def make_channel(left: bool) -> Channel:
    return Channel(id=777, title="Group", photo=ChatPhotoEmpty(), date=datetime.now(timezone.utc),
                   left=left, megagroup=True, access_hash=555)


class AdditionalAccountPosterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tg = AsyncMock()
        self.tg.is_connected = MagicMock(return_value=True)
        self.tg.is_user_authorized.return_value = True
        self.tg.get_me.return_value = SimpleNamespace(bot=False, id=1)
        self.tg.get_entity.return_value = make_channel(left=True)
        self.worker = PosterWorker(account=ACCOUNT, telegram=self.tg, label="Second")
        self.settings = dict(is_running=True, enable_typing_simulation=False, enable_spintax=False,
                             enable_anti_fingerprint=False, min_delay_seconds=15, max_delay_seconds=15, batch_size=10)
        self.chat = dict(id="one", chat_peer="@group", interval_minutes=60)
        self.db = MagicMock()
        self.db.get_settings.side_effect = lambda: dict(self.settings)
        self.db.get_hourly_post_count.return_value = 0
        self.db.get_daily_post_count.return_value = 0
        self.db.get_active_post.return_value = {"content": "Test ad"}
        self.primary = MagicMock()
        for target, value in [("core.poster.db", self.db), ("core.poster.client", self.primary),
                              ("core.poster.asyncio.sleep", AsyncMock())]:
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    async def test_joins_chat_then_sends_html_from_own_client(self):
        outcome = await self.worker._post_to_chat(self.chat, "ad", self.settings, {"source_msg_id": 123})
        self.assertIs(outcome, PostOutcome.SENT)
        join = self.tg.await_args_list[0].args[0]
        self.assertIsInstance(join, JoinChannelRequest)
        peer, text = self.tg.send_message.await_args.args
        self.assertIsInstance(peer, InputPeerChannel)
        self.assertEqual(text, "ad")
        # The source message lives in the primary account's Saved Messages.
        self.tg.get_messages.assert_not_awaited()
        self.primary.send_message.assert_not_called()
        self.db.add_log.assert_called_once()
        self.assertEqual(self.db.add_log.call_args.kwargs["account"], ACCOUNT)

    async def test_member_does_not_join_again(self):
        self.tg.get_entity.return_value = make_channel(left=False)
        await self.worker._post_to_chat(self.chat, "ad", self.settings)
        self.assertFalse(any(isinstance(c.args[0], JoinChannelRequest) for c in self.tg.await_args_list))
        self.tg.send_message.assert_awaited_once()

    async def test_resolved_chat_is_reused_until_it_becomes_unavailable(self):
        await self.worker._post_to_chat(self.chat, "ad", self.settings)
        await self.worker._post_to_chat(self.chat, "ad", self.settings)
        self.assertEqual(self.tg.get_entity.await_count, 1)  # ResolveUsername is strictly rate limited
        self.tg.send_message.side_effect = errors.ChatWriteForbiddenError(None)
        outcome = await self.worker._post_to_chat(self.chat, "ad", self.settings)
        self.assertIs(outcome, PostOutcome.CHAT_UNAVAILABLE)
        self.tg.send_message.side_effect = None
        await self.worker._post_to_chat(self.chat, "ad", self.settings)
        self.assertEqual(self.tg.get_entity.await_count, 2)

    async def test_join_request_defers_chat(self):
        self.tg.side_effect = errors.InviteRequestSentError(None)
        outcome = await self.worker._post_to_chat(self.chat, "ad", self.settings)
        self.assertIs(outcome, PostOutcome.DEFERRED)
        self.db.update_chat_delay.assert_called_once()
        self.tg.send_message.assert_not_awaited()

    async def run_loop(self, chats):
        self.db.get_due_chats.side_effect = [chats, []]
        self.worker._is_active = True
        self.worker._task = asyncio.current_task()

        async def sleep(seconds):
            if self.db.get_due_chats.call_count >= 2 or self.worker._circuit_reason:
                self.worker._is_active = False

        with patch("core.poster.asyncio.sleep", side_effect=sleep):
            await self.worker._run_loop()

    async def test_circuit_breaker_pauses_only_this_account(self):
        self.tg.get_entity.return_value = make_channel(left=False)
        self.tg.send_message.side_effect = ConnectionError("offline")
        await self.run_loop([dict(self.chat, id=str(i), chat_peer=f"@g{i}") for i in range(4)])
        self.assertEqual(self.tg.send_message.await_count, 3)
        self.db.toggle_poster.assert_not_called()
        account_id, updates = self.db.update_account.call_args.args
        self.assertEqual(account_id, ACCOUNT)
        self.assertFalse(updates["posting_enabled"])
        self.db.get_due_chats.assert_called_with(ACCOUNT)
        self.db.get_hourly_post_count.assert_called_with(ACCOUNT)

    async def test_terminated_session_disables_account(self):
        self.tg.is_user_authorized.return_value = False
        await self.run_loop([self.chat])
        self.tg.send_message.assert_not_awaited()
        updates = self.db.update_account.call_args.args[1]
        self.assertEqual(updates["status"], "unauthorized")
        self.assertFalse(updates["posting_enabled"])


class SupervisorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = MagicMock()
        self.db.supports_accounts.return_value = True
        self.account = {"id": ACCOUNT, "label": "Second", "posting_enabled": True, "status": "active",
                        "session_string": "S1"}
        self.db.get_accounts.side_effect = lambda include_session=False: [dict(self.account)]
        self.tg = AsyncMock()
        self.tg.is_user_authorized.return_value = True
        for target, value in [("core.accounts.db", self.db),
                              ("core.accounts.create_account_client", MagicMock(return_value=self.tg)),
                              ("core.accounts.PosterWorker.start", AsyncMock()),
                              ("core.accounts.PosterWorker.stop", AsyncMock())]:
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.supervisor = AccountPosterSupervisor()

    async def test_worker_follows_posting_switch(self):
        await self.supervisor.sync()
        self.assertIn(ACCOUNT, self.supervisor.workers)
        self.assertIs(self.supervisor.workers[ACCOUNT].tg, self.tg)
        await self.supervisor.sync()
        self.assertEqual(len(self.supervisor.workers), 1)
        self.account["posting_enabled"] = False
        await self.supervisor.sync()
        self.assertEqual(self.supervisor.workers, {})
        self.tg.disconnect.assert_awaited()

    async def test_relogin_restarts_worker_with_new_session(self):
        await self.supervisor.sync()
        first = self.supervisor.workers[ACCOUNT]
        self.account["session_string"] = "S2"
        await self.supervisor.sync()
        self.assertIsNot(self.supervisor.workers[ACCOUNT], first)

    async def test_unauthorized_session_disables_account(self):
        self.tg.is_user_authorized.return_value = False
        await self.supervisor.sync()
        self.assertEqual(self.supervisor.workers, {})
        updates = self.db.update_account.call_args.args[1]
        self.assertFalse(updates["posting_enabled"])
        self.assertEqual(updates["status"], "unauthorized")

    async def test_nothing_happens_before_migration(self):
        self.db.supports_accounts.return_value = False
        await self.supervisor.sync()
        self.db.get_accounts.assert_not_called()

    async def test_disabled_workers_never_connect_sessions(self):
        # A local copy sharing the production database must not use the server's sessions.
        with patch("core.accounts.config.ACCOUNT_WORKERS_ENABLED", False):
            await self.supervisor.sync()
        self.assertEqual(self.supervisor.workers, {})
        self.tg.connect.assert_not_awaited()


class LoginTests(unittest.IsolatedAsyncioTestCase):
    def make_login(self, user_id=42):
        login = AccountLogin("phone")
        login.client = AsyncMock()
        login.client.get_me.return_value = SimpleNamespace(
            id=user_id, first_name="New", last_name="", username="new_acc", phone="380", bot=False, premium=False)
        return login

    async def test_finish_stores_session_and_releases_client(self):
        login = self.make_login()
        db = MagicMock()
        db.save_account_session.return_value = {"id": ACCOUNT, "label": "New"}
        with patch("core.accounts.db", db), patch("core.accounts._primary_user_id", AsyncMock(return_value=1)), \
                patch("core.accounts.StringSession.save", return_value="SESSION"):
            await AccountLoginManager()._finish(login)
        self.assertEqual(login.status, "success")
        self.assertEqual(db.save_account_session.call_args.args[1], "SESSION")
        login.client.disconnect.assert_awaited()
        login.client.log_out.assert_not_awaited()

    async def test_primary_account_cannot_be_added_twice(self):
        login = self.make_login(user_id=1)
        db = MagicMock()
        with patch("core.accounts.db", db), patch("core.accounts._primary_user_id", AsyncMock(return_value=1)):
            with self.assertRaises(AccountLoginError):
                await AccountLoginManager()._finish(login)
        db.save_account_session.assert_not_called()
        login.client.log_out.assert_awaited()  # only the redundant temporary session


class AccountsAPITests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from web.api import web_app
        self.http = TestClient(web_app)
        self.db = MagicMock()
        self.db.supports_accounts.return_value = True
        self.db.get_account.return_value = {"id": ACCOUNT, "label": "Second"}
        self.db.get_accounts.return_value = [{"id": ACCOUNT, "label": "Second", "posting_enabled": False}]
        self.db.get_settings.return_value = {"is_running": True}
        self.db.get_all_chats.return_value = [{"id": "c1", "account_id": ACCOUNT, "is_active": True}]
        self.db.get_hourly_post_count.return_value = 0
        self.db.get_daily_post_count.return_value = 0
        self.db.add_chat.return_value = {"id": "c1"}
        primary = MagicMock()
        primary.is_connected.return_value = False
        for target, value in [("web.api.db", self.db), ("web.accounts_api.db", self.db),
                              ("web.accounts_api.client", primary),
                              ("web.accounts_api.account_posters.sync", AsyncMock())]:
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_chat_is_added_to_selected_account(self):
        res = self.http.post("/api/chats", json={"chat_peer": "@x", "interval_minutes": 60, "account_id": ACCOUNT})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.db.add_chat.call_args.kwargs["account"], ACCOUNT)
        self.http.post("/api/chats", json={"chat_peer": "@y", "interval_minutes": 60})
        self.assertEqual(self.db.add_chat.call_args.kwargs["account"], PRIMARY_ACCOUNT)

    def test_conflict_and_unknown_account_are_reported(self):
        self.db.add_chat.side_effect = ChatConflictError("Чат @x уже є у списку: Основний акаунт")
        res = self.http.post("/api/chats", json={"chat_peer": "@x", "account_id": ACCOUNT})
        self.assertEqual(res.status_code, 409)
        self.assertEqual(self.http.get("/api/chats?account=bad").status_code, 400)
        self.db.get_account.return_value = None
        self.assertEqual(self.http.get(f"/api/chats?account={ACCOUNT}").status_code, 404)

    def test_chats_listing_is_scoped(self):
        self.http.get("/api/chats?account=primary")
        self.db.get_all_chats.assert_called_with(PRIMARY_ACCOUNT)
        self.http.get(f"/api/chats?account={ACCOUNT}")
        self.db.get_all_chats.assert_called_with(ACCOUNT)

    def test_accounts_list_never_exposes_sessions(self):
        res = self.http.get("/api/accounts")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["ready"])
        self.assertEqual(data["accounts"][0]["chats_total"], 1)
        self.assertEqual(data["accounts"][0]["poster"]["state"], "disabled")
        self.db.get_accounts.assert_called_with()
        self.assertNotIn("session_string", res.text)

    def test_enabling_posting_clears_previous_pause(self):
        self.db.update_account.return_value = True
        res = self.http.put(f"/api/accounts/{ACCOUNT}", json={"posting_enabled": True})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.db.update_account.call_args.args[1], {"posting_enabled": True, "last_error": None})


if __name__ == "__main__":
    unittest.main()
