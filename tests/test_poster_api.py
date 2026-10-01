import sys
import unittest
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

telegram = sys.modules.setdefault("core.client", ModuleType("core.client"))
telegram.client = MagicMock()
telegram.simulate_typing = AsyncMock()
telegram.get_latest_saved_message = AsyncMock()
telegram.init_telegram_client = AsyncMock()
telegram.reset_client_session = AsyncMock()
telegram.logout_client = AsyncMock()

from fastapi.testclient import TestClient
from web.api import web_app
from database.client import SupabaseDB


class PosterAPITests(unittest.TestCase):
    def setUp(self):
        self.http = TestClient(web_app)
        self.db = MagicMock()
        self.db.get_settings.return_value = {"is_running": False}
        self.db.get_all_chats.return_value = []
        self.db.get_hourly_post_count.return_value = 0
        self.db.get_daily_post_count.return_value = 0
        self.db.get_poster_control_event.return_value = {"status": "circuit_breaker", "details": "Saved cause"}
        self.telegram = MagicMock()
        self.telegram.is_connected.return_value = False
        for target, value in [("web.api.db", self.db), ("core.poster.db", self.db), ("web.api.client", self.telegram)]:
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_status_exposes_saved_cause(self):
        response = self.http.get("/api/status")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["poster"]["reason"], "Saved cause")
        self.assertFalse(response.json()["is_running"])

    def test_failed_toggle_is_not_reported_as_success(self):
        self.db.toggle_poster.return_value = False
        with patch("web.api.poster_worker.reset_errors") as reset:
            response = self.http.post("/api/settings/toggle")
        self.assertEqual(response.status_code, 503)
        reset.assert_not_called()

    def test_resume_resets_errors_after_successful_persistence(self):
        self.db.toggle_poster.return_value = True
        with patch("web.api.poster_worker.reset_errors") as reset:
            response = self.http.post("/api/settings/toggle")
        self.assertEqual(response.json(), {"is_running": True})
        self.db.toggle_poster.assert_called_once_with(True)
        reset.assert_called_once()

    def test_settings_resume_uses_same_control_path(self):
        with patch("web.api.poster_worker.reset_errors") as reset:
            response = self.http.post("/api/settings", json={"is_running": True})
        self.assertEqual(response.status_code, 200)
        self.db.toggle_poster.assert_called_once_with(True)
        reset.assert_called_once()

    def test_failed_settings_save_is_503(self):
        self.db.update_settings.return_value = False
        response = self.http.post("/api/settings", json={"max_posts_per_hour": 20})
        self.assertEqual(response.status_code, 503)


class PosterControlStorageTests(unittest.TestCase):
    def setUp(self):
        self.db = object.__new__(SupabaseDB)
        self.db.update_settings = MagicMock(return_value=True)
        self.db.add_log = MagicMock()

    def test_control_events_distinguish_emergency_manual_pause_and_resume(self):
        for state, reason, expected in [(False, "emergency cause", "circuit_breaker"),
                                        (False, None, "poster_paused"), (True, None, "poster_resumed")]:
            with self.subTest(expected=expected):
                self.assertTrue(self.db.toggle_poster(state, reason=reason))
                self.db.update_settings.assert_called_with({"is_running": state})
                self.assertEqual(self.db.add_log.call_args.args[1], expected)

    def test_failed_update_does_not_log_fictional_state_change(self):
        self.db.update_settings.return_value = False
        self.assertFalse(self.db.toggle_poster(True))
        self.db.add_log.assert_not_called()

    def test_latest_control_event_filter_ignores_unrelated_newer_logs(self):
        import httpx
        from supabase import create_client, ClientOptions

        def handle(request):
            self.assertEqual(request.url.params["status"], "in.(poster_resumed,poster_paused,circuit_breaker)")
            self.assertEqual(request.url.params["order"], "created_at.desc")
            self.assertEqual(request.url.params["limit"], "1")
            return httpx.Response(200, json=[{"status": "circuit_breaker", "details": "saved"}])

        with httpx.Client(transport=httpx.MockTransport(handle)) as http:
            self.db.client = create_client("https://example.supabase.co", "test-key", options=ClientOptions(httpx_client=http))
            self.assertEqual(self.db.get_poster_control_event()["details"], "saved")


if __name__ == "__main__":
    unittest.main()
