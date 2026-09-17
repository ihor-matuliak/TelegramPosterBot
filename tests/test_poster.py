import asyncio
import sys
import unittest
from datetime import datetime, timedelta, timezone
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

# Never open a real Telegram session during import or verification.
telegram = sys.modules.setdefault("core.client", ModuleType("core.client"))
telegram.client = MagicMock()
telegram.simulate_typing = AsyncMock()
telegram.get_latest_saved_message = AsyncMock()

from core.poster import PostOutcome, PosterWorker
from telethon import errors


class PosterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.worker = PosterWorker()
        self.settings = dict(is_running=True, enable_typing_simulation=False,
                             enable_spintax=False, enable_anti_fingerprint=False,
                             min_delay_seconds=15, max_delay_seconds=15, batch_size=10)
        self.chat = dict(id="one", chat_peer="@test", interval_minutes=60)
        self.db = MagicMock()
        self.db.get_settings.side_effect = lambda: dict(self.settings)
        self.db.get_hourly_post_count.return_value = 0
        self.db.get_daily_post_count.return_value = 0
        self.db.get_active_post.return_value = {"content": "Test ad"}
        self.db.get_poster_control_event.return_value = None
        self.db.toggle_poster.side_effect = self.toggle
        self.client = MagicMock()
        self.client.is_connected.return_value = True
        self.client.is_user_authorized = AsyncMock(return_value=True)
        self.client.get_input_entity = AsyncMock(return_value="peer")
        self.client.send_message = AsyncMock()
        self.client.get_messages = AsyncMock()
        for target, value in [("core.poster.db", self.db), ("core.poster.client", self.client)]:
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.waits = []

    def toggle(self, enabled, reason=None):
        self.settings["is_running"] = enabled
        return True

    async def run_chats(self, count):
        chats = [dict(self.chat, id=str(i), chat_peer=f"@test{i}") for i in range(count)]
        self.db.get_due_chats.reset_mock()
        self.db.get_due_chats.side_effect = [chats, []]
        self.worker._is_active = True
        self.worker._task = asyncio.current_task()

        async def sleep(seconds):
            self.waits.append((seconds, self.worker._wait_reason))
            if self.db.get_due_chats.call_count >= 2 or not self.settings["is_running"] or self.worker._circuit_reason:
                self.worker._is_active = False

        with patch("core.poster.asyncio.sleep", side_effect=sleep):
            await self.worker._run_loop()

    async def test_three_restricted_chats_then_healthy_chat_still_sends(self):
        self.client.send_message.side_effect = [
            errors.ChatWriteForbiddenError(None), errors.UserBannedInChannelError(None),
            errors.ChatWriteForbiddenError(None), None,
        ]
        await self.run_chats(4)
        self.assertEqual(self.client.send_message.await_count, 4)
        self.assertEqual(self.db.update_chat.call_count, 3)
        self.assertTrue(all(not call.args[1]["is_active"] for call in self.db.update_chat.call_args_list))
        self.db.update_chat_post_success.assert_called_once()
        self.db.toggle_poster.assert_not_called()
        self.assertTrue(self.settings["is_running"])

    async def test_private_admin_invalid_peers_are_local_failures(self):
        for exc in [errors.ChannelPrivateError(None), errors.ChatAdminRequiredError(None),
                    errors.PeerIdInvalidError(None), errors.UsernameInvalidError(None),
                    errors.UsernameNotOccupiedError(None), ValueError("Unknown entity")]:
            with self.subTest(error=type(exc).__name__):
                self.client.get_input_entity.side_effect = exc
                outcome = await self.worker._post_to_chat(self.chat, "ad", self.settings)
                self.assertIs(outcome, PostOutcome.CHAT_UNAVAILABLE)
        self.client.send_message.assert_not_awaited()

    async def test_three_system_errors_pause_before_fourth_send(self):
        self.client.send_message.side_effect = errors.RPCError(None, "ACCOUNT_FAILURE", 500)
        await self.run_chats(4)
        self.assertEqual(self.client.send_message.await_count, 3)
        self.assertFalse(self.settings["is_running"])
        self.db.toggle_poster.assert_called_once()
        self.assertIn("3", self.db.toggle_poster.call_args.kwargs["reason"])
        self.assertEqual(self.worker._consecutive_errors, 0)

    async def test_failed_emergency_pause_persistence_still_stops_local_worker(self):
        self.db.toggle_poster.side_effect = None
        self.db.toggle_poster.return_value = False
        self.client.send_message.side_effect = ConnectionError("offline")
        await self.run_chats(4)
        self.assertEqual(self.client.send_message.await_count, 3)
        self.assertIsNotNone(self.worker._circuit_reason)
        self.assertEqual(self.worker.get_status(self.settings, True, 0, 0)["state"], "paused")

    async def test_resume_does_not_inherit_error_counter(self):
        self.worker._consecutive_errors = 2
        self.worker._circuit_reason = "previous failure"
        self.worker.reset_errors()
        self.client.send_message.side_effect = [ConnectionError("offline"), None]
        await self.run_chats(2)
        self.db.toggle_poster.assert_not_called()
        self.db.update_chat_post_success.assert_called_once()

    async def test_success_breaks_system_error_streak(self):
        self.client.send_message.side_effect = [ConnectionError(), ConnectionError(), None, ConnectionError()]
        await self.run_chats(4)
        self.db.toggle_poster.assert_not_called()
        self.assertEqual(self.worker._consecutive_errors, 1)

    async def test_three_slowmode_waits_do_not_pause_other_chats(self):
        self.client.send_message.side_effect = [errors.SlowModeWaitError(None, 40)] * 3 + [None]
        await self.run_chats(4)
        self.db.toggle_poster.assert_not_called()
        self.assertEqual(self.db.update_chat_delay.call_count, 3)
        self.assertTrue(all(c.args[1] == 45 for c in self.db.update_chat_delay.call_args_list))
        self.assertTrue(all(seconds == 15 for seconds, reason in self.waits if reason))
        self.db.update_chat_post_success.assert_called_once()

    async def test_three_flood_waits_wait_with_margin_and_do_not_trip_breaker(self):
        self.client.send_message.side_effect = [errors.FloodWaitError(None, 40)] * 3 + [None]
        await self.run_chats(4)
        self.db.toggle_poster.assert_not_called()
        flood_waits = [seconds for seconds, reason in self.waits if reason and "FloodWait" in reason]
        self.assertEqual(flood_waits, [42, 42, 42])
        self.db.update_chat_post_success.assert_called_once()

    async def test_native_send_errors_never_retry_immediately_as_text(self):
        self.client.get_messages.return_value = SimpleNamespace(message="native", text="native", entities=[], media=None)
        for exc, expected in [(errors.FloodWaitError(None, 20), PostOutcome.DEFERRED),
                              (errors.SlowModeWaitError(None, 20), PostOutcome.DEFERRED),
                              (errors.ChatWriteForbiddenError(None), PostOutcome.CHAT_UNAVAILABLE),
                              (ConnectionError(), PostOutcome.SYSTEM_ERROR)]:
            with self.subTest(error=type(exc).__name__):
                self.client.send_message.reset_mock()
                self.client.send_message.side_effect = exc
                with patch("core.poster.asyncio.sleep", new=AsyncMock()):
                    outcome = await self.worker._post_to_chat(self.chat, "fallback", self.settings, {"source_msg_id": 123})
                self.assertIs(outcome, expected)
                self.client.send_message.assert_awaited_once()

    async def test_source_fetch_flood_wait_does_not_send_fallback(self):
        self.client.get_messages.side_effect = errors.FloodWaitError(None, 20)
        with patch("core.poster.asyncio.sleep", new=AsyncMock()) as sleep:
            result = await self.worker._post_to_chat(self.chat, "fallback", self.settings, {"source_msg_id": 123})
        self.assertIs(result, PostOutcome.DEFERRED)
        sleep.assert_awaited_once_with(22)
        self.client.send_message.assert_not_awaited()

    async def test_missing_source_uses_fallback_text(self):
        self.client.get_messages.return_value = None
        result = await self.worker._post_to_chat(self.chat, "fallback", self.settings, {"source_msg_id": 123})
        self.assertIs(result, PostOutcome.SENT)
        self.client.send_message.assert_awaited_once_with("peer", "fallback", parse_mode="html")

    async def test_cancelled_flood_wait_clears_runtime_wait(self):
        self.client.send_message.side_effect = errors.FloodWaitError(None, 120)
        task = asyncio.create_task(self.worker._post_to_chat(self.chat, "ad", self.settings))
        await asyncio.sleep(0)
        self.assertIsNotNone(self.worker._wait_until)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertIsNone(self.worker._wait_until)

    async def test_batch_limit_is_enforced_inside_fetched_batch(self):
        self.settings["batch_size"] = 2
        self.settings["batch_rest_minutes"] = 1
        await self.run_chats(4)
        self.assertEqual(self.client.send_message.await_count, 2)
        self.assertIn((60, "Захисна пауза між серіями публікацій"), self.waits)

    async def test_daily_limit_stops_fetched_batch(self):
        self.settings["max_posts_per_day"] = 1
        self.db.get_daily_post_count.side_effect = lambda: self.client.send_message.await_count
        async def wait(seconds, reason):
            if "добового" in reason:
                self.worker._is_active = False
        with patch.object(self.worker, "_wait", side_effect=wait):
            await self.run_chats(4)
        self.assertEqual(self.client.send_message.await_count, 1)

    async def test_paused_loop_resets_old_errors_and_sends_nothing(self):
        self.settings["is_running"] = False
        self.worker._consecutive_errors = 2
        await self.run_chats(4)
        self.client.send_message.assert_not_awaited()
        self.assertEqual(self.worker._consecutive_errors, 0)

    async def test_status_reports_actual_blockers_and_persisted_pause(self):
        self.db.get_poster_control_event.return_value = {"status": "circuit_breaker", "details": "Saved cause"}
        status = self.worker.get_status({"is_running": False}, True, 0, 0)
        self.assertEqual(status["reason"], "Saved cause")
        self.assertEqual(self.worker.get_status(self.settings, True, 0, 0)["state"], "stopped")
        self.worker._is_active = True
        self.worker._task = asyncio.current_task()
        self.assertEqual(self.worker.get_status(self.settings, False, 0, 0)["state"], "unauthorized")
        self.assertEqual(self.worker.get_status(self.settings, True, 100, 0)["state"], "hourly_limit")
        self.assertEqual(self.worker.get_status(self.settings, True, 0, 999)["state"], "daily_limit")
        self.worker._wait_reason = "FloodWait"
        self.worker._wait_until = datetime.now(timezone.utc) + timedelta(seconds=10)
        self.assertEqual(self.worker.get_status(self.settings, True, 0, 0)["reason"], "FloodWait")
        self.worker._wait_until -= timedelta(seconds=20)
        self.assertEqual(self.worker.get_status(self.settings, True, 0, 0)["state"], "running")


if __name__ == "__main__":
    unittest.main()
