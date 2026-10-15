"""Additional posting accounts API: list, enable/disable, delete and add (phone code or QR)."""

import logging
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from config import config
from core.accounts import AccountLoginError, account_logins, account_posters
from core.client import client
from core.poster import poster_worker
from database.client import db, PRIMARY_ACCOUNT

logger = logging.getLogger("AccountsAPI")
router = APIRouter(prefix="/api/accounts")


def resolve_account_key(value: Optional[str]) -> str:
    """Map an API account parameter ('primary', empty or an account UUID) to a database scope key."""
    if value in (None, "", PRIMARY_ACCOUNT, "default"):
        return PRIMARY_ACCOUNT
    try:
        account_id = str(UUID(value))
    except ValueError:
        raise HTTPException(400, "Некоректний ідентифікатор акаунта.")
    if not db.supports_accounts():
        raise HTTPException(503, "Мульти-акаунти ще не активовані: виконайте міграцію бази даних.")
    if not db.get_account(account_id):
        raise HTTPException(404, "Акаунт не знайдено.")
    return account_id


def _chat_counts(chats: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for chat in chats:
        key = chat.get("account_id") or PRIMARY_ACCOUNT
        bucket = counts.setdefault(key, {"chats_total": 0, "chats_active": 0, "chats_error": 0})
        bucket["chats_total"] += 1
        bucket["chats_active"] += 1 if chat.get("is_active") else 0
        bucket["chats_error"] += 1 if chat.get("status") in ("error", "restricted") else 0
    return counts


async def _primary_user() -> Optional[dict[str, Any]]:
    try:
        if client.is_connected() and await client.is_user_authorized():
            me = await client.get_me()
            return {
                "id": me.id,
                "name": f"{me.first_name} {me.last_name or ''}".strip(),
                "username": me.username,
                "phone": me.phone,
                "is_premium": getattr(me, "premium", False),
            }
    except Exception as e:
        logger.warning(f"Could not read primary account profile: {e}")
    return None


def _login_response(state: dict[str, Any]) -> dict[str, Any]:
    if state.get("method") == "phone" and state.get("status") == "code_sent":
        from web.api import describe_sent_code_type
        login = account_logins.get(state["login_id"])
        state["delivery_text"] = describe_sent_code_type(login.sent_code_type)
    return state


class AccountUpdateRequest(BaseModel):
    label: Optional[str] = Field(default=None, max_length=80)
    posting_enabled: Optional[bool] = None


class PhoneLoginRequest(BaseModel):
    phone: str


class CodeRequest(BaseModel):
    code: str


class PasswordRequest(BaseModel):
    password: str


@router.get("")
async def list_accounts() -> dict:
    settings = db.get_settings()
    counts = _chat_counts(db.get_all_chats())
    empty = {"chats_total": 0, "chats_active": 0, "chats_error": 0}

    user = await _primary_user()
    p_hourly = db.get_hourly_post_count(PRIMARY_ACCOUNT)
    p_daily = db.get_daily_post_count(PRIMARY_ACCOUNT)
    primary = {
        "key": PRIMARY_ACCOUNT,
        "label": "Основний акаунт",
        "is_primary": True,
        "user": user,
        "posting_enabled": True,
        "hourly": p_hourly,
        "daily": p_daily,
        "poster": poster_worker.get_status(settings, user is not None, p_hourly, p_daily),
        **counts.get(PRIMARY_ACCOUNT, empty),
    }

    accounts = []
    for account in db.get_accounts():
        hourly = db.get_hourly_post_count(account["id"])
        daily = db.get_daily_post_count(account["id"])
        accounts.append({
            **account,
            "key": account["id"],
            "is_primary": False,
            "hourly": hourly,
            "daily": daily,
            "poster": account_posters.worker_status(account["id"], settings, hourly, daily),
            **counts.get(account["id"], empty),
        })

    return {
        "ready": bool(db.supports_accounts()),
        "workers_enabled": config.ACCOUNT_WORKERS_ENABLED,
        "primary": primary,
        "accounts": accounts,
    }


@router.put("/{account_id}")
async def update_account(account_id: UUID, req: AccountUpdateRequest) -> dict:
    key = resolve_account_key(str(account_id))
    updates: dict[str, Any] = {}
    if req.label is not None:
        label = req.label.strip()
        if not label:
            raise HTTPException(400, "Назва акаунта не може бути порожньою.")
        updates["label"] = label
    if req.posting_enabled is not None:
        updates["posting_enabled"] = req.posting_enabled
        if req.posting_enabled:
            # Re-enabling clears a previous automatic pause; the supervisor re-checks the session.
            updates["last_error"] = None
    if not updates:
        return {"status": "ok"}
    if not db.update_account(key, updates):
        raise HTTPException(503, "Не вдалося зберегти зміни акаунта.")
    db.add_log("SYSTEM", "account_updated",
               f"Акаунт {(db.get_account(key) or {}).get('label', key)}: "
               + ("розсилку увімкнено" if req.posting_enabled else "розсилку вимкнено" if req.posting_enabled is False else "оновлено"),
               account=key)
    await account_posters.sync()
    return {"status": "ok", "account": db.get_account(key)}


@router.delete("/{account_id}")
async def delete_account(account_id: UUID, logout: bool = True) -> dict:
    key = resolve_account_key(str(account_id))
    label = (db.get_account(key) or {}).get("label", key)
    if not await account_posters.remove_account(key, logout=logout):
        raise HTTPException(503, "Не вдалося видалити акаунт.")
    db.add_log("SYSTEM", "account_removed", f"Видалено акаунт {label} разом з його чатами")
    return {"status": "ok"}


# ==================== ADD ACCOUNT (LOGIN FLOWS) ====================

@router.post("/login/phone")
async def login_phone(req: PhoneLoginRequest) -> dict:
    from web.api import normalize_phone
    phone = normalize_phone(req.phone)
    if not phone or len(phone) < 8:
        raise HTTPException(400, "Введіть коректний номер телефону (наприклад: +380991234567)")
    try:
        login = await account_logins.start_phone(phone)
    except AccountLoginError as e:
        raise HTTPException(400, str(e))
    return _login_response(login.state())


@router.post("/login/qr")
async def login_qr() -> dict:
    try:
        login = await account_logins.start_qr()
    except AccountLoginError as e:
        raise HTTPException(400, str(e))
    return login.state()


@router.get("/login/{login_id}")
async def login_status(login_id: str) -> dict:
    try:
        return account_logins.get(login_id).state()
    except AccountLoginError as e:
        raise HTTPException(404, str(e))


@router.post("/login/{login_id}/qr/refresh")
async def login_qr_refresh(login_id: str) -> dict:
    try:
        return (await account_logins.refresh_qr(login_id)).state()
    except AccountLoginError as e:
        raise HTTPException(400, str(e))


@router.post("/login/{login_id}/code")
async def login_code(login_id: str, req: CodeRequest) -> dict:
    try:
        return (await account_logins.submit_code(login_id, req.code)).state()
    except AccountLoginError as e:
        raise HTTPException(400, str(e))


@router.post("/login/{login_id}/password")
async def login_password(login_id: str, req: PasswordRequest) -> dict:
    try:
        return (await account_logins.submit_password(login_id, req.password)).state()
    except AccountLoginError as e:
        raise HTTPException(400, str(e))


@router.delete("/login/{login_id}")
async def login_cancel(login_id: str) -> dict:
    await account_logins.cancel(login_id)
    return {"status": "ok"}
