import sys
import unittest
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

telegram_module = sys.modules.setdefault("core.client", ModuleType("core.client"))
telegram_module.client = MagicMock()
telegram_module.simulate_typing = AsyncMock()
telegram_module.get_latest_saved_message = AsyncMock()

from fastapi.testclient import TestClient
from web.api import web_app
from database.discovery import DiscoveryStorageError


class DiscoveryAPITests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(web_app)

    def test_start_contract(self):
        sid = str(uuid4())
        with patch("web.discovery_api.manager.start", new=AsyncMock(return_value={"id": sid, "status": "pending"})) as start:
            response = self.client.post("/api/discovery/search", json={"query": "OFM", "min_members": 500})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["search_id"], sid)
        start.assert_awaited_once_with("OFM", 500)

    def test_invalid_minimum_rejected(self):
        self.assertEqual(self.client.post("/api/discovery/search", json={"query": "OFM", "min_members": -1}).status_code, 422)

    def test_database_failure_is_503(self):
        with patch("web.discovery_api.manager.start", new=AsyncMock(side_effect=DiscoveryStorageError("Migration required"))):
            response = self.client.post("/api/discovery/search", json={"query": "OFM"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "Migration required")

    def test_add_forwards_selected_post(self):
        pid, rid = str(uuid4()), str(uuid4())
        with patch("web.discovery_api.ChatFinder.join_and_add_chat", new=AsyncMock(return_value={"status": "ok"})) as add, patch("web.discovery_api.repository.set_result_status", new=AsyncMock()):
            response = self.client.post("/api/discovery/add-to-posting", json={"result_id": rid, "chat_peer": "@ofm", "post_id": pid, "title": "Agency's chat", "interval_minutes": 1440})
        self.assertEqual(response.status_code, 200)
        add.assert_awaited_once_with("@ofm", 1440, "Agency's chat", pid)

    def test_stop_contract(self):
        sid = str(uuid4())
        with patch("web.discovery_api.manager.stop", new=AsyncMock()) as stop:
            response = self.client.post(f"/api/discovery/search/{sid}/stop")
        stop.assert_awaited_once_with(sid)
        self.assertEqual(response.json()["status"], "stopped")

    def test_history_pagination(self):
        with patch("web.discovery_api.repository.results", new=AsyncMock(return_value={"results": [], "total": 80})) as results:
            response = self.client.get("/api/discovery/history?offset=50&limit=30&decision=all&include_hidden=true")
        results.assert_awaited_once_with(None, 50, 30, "all", True)
        self.assertEqual(response.status_code, 200)

    def test_obsolete_endpoint_has_clear_error(self):
        response = self.client.post("/api/finder/search", json={"query": "OFM"})
        self.assertEqual(response.status_code, 410)


if __name__ == "__main__":
    unittest.main()
