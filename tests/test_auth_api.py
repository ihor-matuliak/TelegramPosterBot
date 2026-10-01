import sys
import unittest
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

telegram = sys.modules.setdefault("core.client", ModuleType("core.client"))
telegram.client = MagicMock()
telegram.simulate_typing = AsyncMock()
telegram.get_latest_saved_message = AsyncMock()
telegram.init_telegram_client = AsyncMock()
telegram.reset_client_session = AsyncMock(return_value=True)
telegram.logout_client = AsyncMock(return_value=True)
telegram.login_with_bot_token = AsyncMock(return_value={
    "status": "ok",
    "message": "Authorized as bot",
    "user": {"id": 8956589067, "name": "Bot", "username": "reddfan_poster_bot", "is_bot": True}
})

from fastapi.testclient import TestClient
from telethon.errors import (
    PhoneNumberInvalidError,
    PhoneCodeInvalidError,
    SessionPasswordNeededError,
    PasswordHashInvalidError
)
from web.api import web_app


class AuthAPITests(unittest.TestCase):
    def setUp(self):
        self.http = TestClient(web_app)
        self.mock_client = AsyncMock()
        self.mock_client.is_connected = MagicMock(return_value=True)
        patcher = patch("web.api.client", self.mock_client)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_send_code_invalid_phone(self):
        res = self.http.post("/api/auth/send-code", json={"phone": "123"})
        self.assertEqual(res.status_code, 400)
        self.assertIn("номер телефону", res.json()["detail"])

    def test_send_code_success(self):
        fake_sent = MagicMock()
        fake_sent.phone_code_hash = "hash_12345"
        self.mock_client.send_code_request = AsyncMock(return_value=fake_sent)

        res = self.http.post("/api/auth/send-code", json={"phone": "+380991234567"})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["phone_code_hash"], "hash_12345")
        self.assertEqual(data["phone"], "+380991234567")

    def test_send_code_phone_invalid_telegram_error(self):
        self.mock_client.send_code_request = AsyncMock(side_effect=PhoneNumberInvalidError(request=None))

        res = self.http.post("/api/auth/send-code", json={"phone": "+380000000000"})
        self.assertEqual(res.status_code, 400)
        self.assertIn("Невірний номер телефону", res.json()["detail"])

    def test_verify_code_success(self):
        fake_me = MagicMock()
        fake_me.id = 999888
        fake_me.first_name = "Ihor"
        fake_me.last_name = "Dev"
        fake_me.username = "ihordev"
        fake_me.phone = "380991234567"
        fake_me.premium = True

        self.mock_client.sign_in = AsyncMock()
        self.mock_client.get_me = AsyncMock(return_value=fake_me)

        res = self.http.post(
            "/api/auth/verify-code",
            json={"phone": "+380991234567", "code": "54321", "phone_code_hash": "hash_123"}
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["user"]["name"], "Ihor Dev")
        self.assertEqual(data["user"]["username"], "ihordev")
        self.assertTrue(data["user"]["is_premium"])

    def test_verify_code_needs_2fa(self):
        self.mock_client.sign_in = AsyncMock(side_effect=SessionPasswordNeededError(request=None))

        res = self.http.post(
            "/api/auth/verify-code",
            json={"phone": "+380991234567", "code": "54321"}
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "needs_2fa")

    def test_verify_code_invalid(self):
        self.mock_client.sign_in = AsyncMock(side_effect=PhoneCodeInvalidError(request=None))

        res = self.http.post(
            "/api/auth/verify-code",
            json={"phone": "+380991234567", "code": "00000"}
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("Невірний код", res.json()["detail"])

    def test_verify_2fa_success(self):
        fake_me = MagicMock()
        fake_me.id = 111222
        fake_me.first_name = "Admin"
        fake_me.last_name = None
        fake_me.username = "adminuser"
        fake_me.phone = "380991234567"
        fake_me.premium = False

        self.mock_client.sign_in = AsyncMock()
        self.mock_client.get_me = AsyncMock(return_value=fake_me)

        res = self.http.post("/api/auth/verify-2fa", json={"password": "mypassword"})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["user"]["username"], "adminuser")

    def test_verify_2fa_invalid_password(self):
        self.mock_client.sign_in = AsyncMock(side_effect=PasswordHashInvalidError(request=None))

        res = self.http.post("/api/auth/verify-2fa", json={"password": "wrongpassword"})
        self.assertEqual(res.status_code, 400)
        self.assertIn("Невірний пароль", res.json()["detail"])

    def test_logout(self):
        res = self.http.post("/api/auth/logout")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["status"], "ok")

    def test_reset_session(self):
        res = self.http.post("/api/auth/reset-session")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["status"], "ok")

    def test_login_bot_token(self):
        res = self.http.post("/api/auth/login-bot-token", json={})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["user"]["username"], "reddfan_poster_bot")


if __name__ == "__main__":
    unittest.main()
