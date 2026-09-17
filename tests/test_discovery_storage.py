import json
import unittest

import httpx
from supabase import create_client, ClientOptions

from core.discovery_relevance import qualify
from database.discovery import DiscoveryRepository, DiscoveryStorageError


class StorageContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_upsert_preserves_first_discovery_and_does_not_send_raw_messages(self):
        rows = {}
        requests = []
        def handle(request):
            requests.append(request)
            self.assertEqual(request.url.params["on_conflict"], "telegram_id")
            self.assertIn("resolution=ignore-duplicates", request.headers["prefer"])
            row = json.loads(request.content)
            self.assertNotIn("messages", row)
            self.assertNotIn("_entity", row)
            tid = row["telegram_id"]
            response = [] if tid in rows else [row]
            rows.setdefault(tid, row)
            return httpx.Response(201, json=response)
        http = httpx.Client(transport=httpx.MockTransport(handle))
        db = create_client("https://example.supabase.co", "test-key", options=ClientOptions(httpx_client=http))
        repo = DiscoveryRepository(db)
        candidate = qualify(dict(telegram_id=-10042, title="OFM agency", peer="@agency", type="group",
            participants_count=None, description="OnlyFans management", messages=["Reddit account suppliers"],
            posting_access="unknown", can_post=False))
        self.assertTrue(await repo.save_candidate("first", candidate))
        self.assertFalse(await repo.save_candidate("second", {**candidate, "peer": "@renamed"}))
        self.assertEqual(rows[-10042]["search_id"], "first")
        self.assertIsNone(rows[-10042]["members_count"])
        http.close()

    async def test_empty_update_cannot_be_reported_as_saved(self):
        http = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[])))
        db = create_client("https://example.supabase.co", "test-key", options=ClientOptions(httpx_client=http))
        repo = DiscoveryRepository(db)
        row = qualify(dict(telegram_id=-10042, title="OFM", peer="@agency", type="group", messages=[]))
        with self.assertRaises(DiscoveryStorageError): await repo.update_candidate("first", row)
        http.close()


if __name__ == "__main__":
    unittest.main()
