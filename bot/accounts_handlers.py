"""Admin bot: additional posting accounts and their own chat lists."""

import logging
import re
from html import escape
from typing import Any, Optional

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from config import config
from core.accounts import AccountLoginError, account_logins, account_posters
from core.client import describe_sent_code_type, normalize_phone
from database.client import ACCOUNTS_MIGRATION, PRIMARY_ACCOUNT, ChatConflictError, db

logger = logging.getLogger("AdminBot.Accounts")
router = Router()

PAGE_SIZE = 10
INTERVALS = [(60, "1 год"), (120, "2 год"), (360, "6 год"), (720, "12 год"), (1440, "1 доба"), (4320, "3 доби")]


class AccountStates(StatesGroup):
    waiting_for_phone = State()
    waiting_for_code = State()
    waiting_for_2fa = State()
    waiting_for_chat_links = State()


def is_admin(user_id: int) -> bool:
    admin_ids = config.get_admin_ids()
    return not admin_ids or user_id in admin_ids


async def _deny(call: CallbackQuery) -> bool:
    if is_admin(call.from_user.id):
        return False
    await call.answer("🚫 Доступ заборонено.", show_alert=True)
    return True


def _label(key: str) -> str:
    if key == PRIMARY_ACCOUNT:
        return "⭐ Основний акаунт"
    return (db.get_account(key) or {}).get("label") or "Акаунт"


def _valid_key(key: str) -> bool:
    return key == PRIMARY_ACCOUNT or bool(db.get_account(key))


def parse_chat_links(text: str) -> list[str]:
    """Same parsing rules as the web batch import."""
    peers = []
    for raw_line in text.strip().split("\n"):
        line = raw_line.strip()
        match = re.search(r"(?:https?://t\.me/)?(@?[a-zA-Z0-9_+\-]+)", line) if line else None
        if match:
            peer = match.group(1)
            if not peer.startswith("+") and not peer.startswith("@") and not peer.startswith("joinchat"):
                peer = f"@{peer}"
            if peer not in peers:
                peers.append(peer)
    return peers


async def _edit(call: CallbackQuery, text: str, markup: InlineKeyboardMarkup) -> None:
    try:
        await call.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    except Exception:
        await call.message.answer(text, reply_markup=markup, parse_mode="HTML")


def _back_to_accounts() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ До акаунтів", callback_data="acc_list")]])


def _cancel_login_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Скасувати", callback_data="acc_login_cancel")]])


# ==================== ACCOUNTS LIST & DETAILS ====================

def _accounts_view() -> tuple[str, InlineKeyboardMarkup]:
    chats = db.get_all_chats()
    totals: dict[str, int] = {}
    for c in chats:
        key = c.get("account_id") or PRIMARY_ACCOUNT
        totals[key] = totals.get(key, 0) + 1

    lines = ["👥 <b>Акаунти для розсилки</b>\n"]
    keyboard = [[InlineKeyboardButton(text=f"⭐ Основний акаунт ({totals.get(PRIMARY_ACCOUNT, 0)} чатів)",
                                      callback_data=f"acc_view:{PRIMARY_ACCOUNT}")]]
    if not db.supports_accounts():
        lines.append(
            "⚠️ Додаткові акаунти ще не активовані.\n"
            f"Виконайте міграцію <code>{ACCOUNTS_MIGRATION}</code> у Supabase SQL Editor."
        )
        keyboard.append([InlineKeyboardButton(text="⬅️ Головне меню", callback_data="btn_refresh")])
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=keyboard)

    accounts = db.get_accounts()
    if not accounts:
        lines.append("<i>Додаткових акаунтів ще немає. Натисніть «➕ Додати акаунт».</i>")
    for acc in accounts:
        icon = "🟢" if acc.get("posting_enabled") else ("⚠️" if acc.get("status") != "active" else "⏸️")
        text = f"{icon} {acc.get('label') or acc['id'][:8]} ({totals.get(acc['id'], 0)} чатів)"
        keyboard.append([InlineKeyboardButton(text=text[:60], callback_data=f"acc_view:{acc['id']}")])
    lines.append("\n🟢 розсилка увімкнена • ⏸️ вимкнена • ⚠️ потрібна увага")
    if not config.ACCOUNT_WORKERS_ENABLED:
        lines.append("\n⚠️ На цьому сервері ACCOUNT_WORKERS_ENABLED=false — додаткові акаунти тут не постять.")
    keyboard.append([InlineKeyboardButton(text="➕ Додати акаунт", callback_data="acc_add")])
    keyboard.append([InlineKeyboardButton(text="⬅️ Головне меню", callback_data="btn_refresh")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=keyboard)


@router.callback_query(F.data == "acc_list")
async def cb_accounts_list(call: CallbackQuery, state: FSMContext):
    if await _deny(call):
        return
    await state.clear()
    text, markup = _accounts_view()
    await _edit(call, text, markup)
    await call.answer()


def _account_view(key: str) -> tuple[str, InlineKeyboardMarkup]:
    chats = db.get_all_chats(key)
    active = sum(1 for c in chats if c.get("is_active"))
    errors = sum(1 for c in chats if c.get("status") in ("error", "restricted"))
    hourly, daily = db.get_hourly_post_count(key), db.get_daily_post_count(key)
    settings = db.get_settings()
    stats = (
        f"• Чатів: <b>{len(chats)}</b> (активних: {active}, з помилками: {errors})\n"
        f"• Постів: <b>{hourly}</b>/{settings.get('max_posts_per_hour', 30)} за годину, "
        f"<b>{daily}</b>/{settings.get('max_posts_per_day', 300)} за добу\n"
    )
    keyboard = [
        [InlineKeyboardButton(text="💬 Чати акаунта", callback_data=f"acc_chats:{key}:0"),
         InlineKeyboardButton(text="➕ Додати чати", callback_data=f"acc_addchats:{key}")],
    ]
    if key == PRIMARY_ACCOUNT:
        text = (
            "⭐ <b>Основний акаунт</b>\n\n"
            f"{stats}"
            "• Розсилка керується головним перемикачем у меню.\n"
            "• Пости зі «Збережених» відправляються з повною копією (емодзі, медіа)."
        )
        keyboard.append([InlineKeyboardButton(text="👤 Сесія основного акаунта", callback_data="btn_tg_auth")])
    else:
        acc = db.get_account(key) or {}
        enabled = acc.get("posting_enabled")
        status = {"active": "🟢 сесія активна", "unauthorized": "🔴 сесію завершено", "error": "⚠️ помилка"}.get(
            acc.get("status"), acc.get("status") or "—")
        poster = account_posters.worker_status(key, settings, hourly, daily)
        username = f"@{acc['username']}" if acc.get("username") else "без юзернейму"
        phone = f"+{acc['phone']}" if acc.get("phone") else "—"
        text = (
            f"👤 <b>{escape(acc.get('label') or 'Акаунт')}</b>\n\n"
            f"• Профіль: <b>{escape(acc.get('first_name') or '')}</b> ({escape(username)}), <code>{escape(phone)}</code>\n"
            f"• Сесія: {status}\n"
            f"• Розсилка з акаунта: <b>{'увімкнена' if enabled else 'вимкнена'}</b>\n"
            f"• Стан: {escape(poster.get('reason') or 'працює')}\n"
            f"{stats}"
        )
        if acc.get("last_error"):
            text += f"• Остання помилка: <i>{escape(acc['last_error'])}</i>\n"
        text += "\n<i>Пости акаунт бере із загального списку варіантів. Перед першим постом він сам вступає в чат.</i>"
        keyboard.insert(0, [InlineKeyboardButton(
            text="⏸️ Вимкнути розсилку" if enabled else "▶️ Увімкнути розсилку", callback_data=f"acc_toggle:{key}")])
        keyboard.append([InlineKeyboardButton(text="🗑️ Видалити акаунт", callback_data=f"acc_del:{key}")])
    keyboard.append([InlineKeyboardButton(text="⬅️ До акаунтів", callback_data="acc_list")])
    return text, InlineKeyboardMarkup(inline_keyboard=keyboard)


@router.callback_query(F.data.startswith("acc_view:"))
async def cb_account_view(call: CallbackQuery, state: FSMContext):
    if await _deny(call):
        return
    await state.clear()
    key = call.data.split(":", 1)[1]
    if not _valid_key(key):
        await call.answer("Акаунт не знайдено", show_alert=True)
        return
    text, markup = _account_view(key)
    await _edit(call, text, markup)
    await call.answer()


@router.callback_query(F.data.startswith("acc_toggle:"))
async def cb_account_toggle(call: CallbackQuery):
    if await _deny(call):
        return
    key = call.data.split(":", 1)[1]
    acc = db.get_account(key)
    if not acc:
        await call.answer("Акаунт не знайдено", show_alert=True)
        return
    enable = not acc.get("posting_enabled")
    updates: dict[str, Any] = {"posting_enabled": enable}
    if enable:
        updates["last_error"] = None
    if not db.update_account(key, updates):
        await call.answer("⚠️ Не вдалося зберегти. Спробуйте ще раз.", show_alert=True)
        return
    db.add_log("SYSTEM", "account_updated",
               f"Акаунт {acc.get('label')}: розсилку {'увімкнено' if enable else 'вимкнено'}", account=key)
    await account_posters.sync()
    text, markup = _account_view(key)
    await _edit(call, text, markup)
    hint = "" if db.get_settings().get("is_running") else "\nГоловний перемикач розсилки зараз на паузі."
    await call.answer(("▶️ Розсилку з акаунта увімкнено" if enable else "⏸️ Розсилку з акаунта вимкнено") + hint,
                      show_alert=True)


@router.callback_query(F.data.startswith("acc_del:"))
async def cb_account_delete_confirm(call: CallbackQuery):
    if await _deny(call):
        return
    key = call.data.split(":", 1)[1]
    acc = db.get_account(key)
    if not acc:
        await call.answer("Акаунт не знайдено", show_alert=True)
        return
    count = len(db.get_all_chats(key))
    text = (
        f"🗑️ <b>Видалити акаунт «{escape(acc.get('label') or '')}»?</b>\n\n"
        f"• Сесію буде завершено в Telegram.\n"
        f"• Разом з акаунтом буде видалено його список чатів (<b>{count}</b>).\n"
        f"• Загальні варіанти постів не зміняться."
    )
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑️ Так, видалити", callback_data=f"acc_delok:{key}")],
        [InlineKeyboardButton(text="⬅️ Скасувати", callback_data=f"acc_view:{key}")],
    ])
    await _edit(call, text, markup)
    await call.answer()


@router.callback_query(F.data.startswith("acc_delok:"))
async def cb_account_delete(call: CallbackQuery):
    if await _deny(call):
        return
    key = call.data.split(":", 1)[1]
    label = _label(key)
    await call.answer("⏳ Видалення...")
    if await account_posters.remove_account(key, logout=True):
        db.add_log("SYSTEM", "account_removed", f"Видалено акаунт {label} разом з його чатами")
        notice = f"🗑️ Акаунт «{escape(label)}» видалено.\n\n"
    else:
        notice = "⚠️ Не вдалося видалити акаунт.\n\n"
    text, markup = _accounts_view()
    await _edit(call, notice + text, markup)


# ==================== CHATS OF ONE ACCOUNT ====================

def _chats_view(key: str, page: int) -> tuple[str, InlineKeyboardMarkup]:
    chats = db.get_all_chats(key)
    posts = {p["id"]: p.get("title", "Пост") for p in db.get_all_posts()}
    total_pages = max(1, (len(chats) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    keyboard = []
    for c in chats[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]:
        icon = "🟢" if c.get("is_active") else "⏸️"
        if c.get("status") in ("error", "restricted"):
            icon = "🚫"
        label = f"{icon} {c['chat_peer']} ➔ [{posts.get(c.get('post_id'), '⭐️ Дефолт')}]"
        keyboard.append([InlineKeyboardButton(text=label[:60], callback_data=f"acc_chat:{c['id']}")])
    if total_pages > 1:
        keyboard.append([
            InlineKeyboardButton(text="⬅️", callback_data=f"acc_chats:{key}:{page - 1}") if page > 0
            else InlineKeyboardButton(text="⏹️", callback_data="noop"),
            InlineKeyboardButton(text=f"📄 {page + 1}/{total_pages}", callback_data="noop"),
            InlineKeyboardButton(text="➡️", callback_data=f"acc_chats:{key}:{page + 1}") if page < total_pages - 1
            else InlineKeyboardButton(text="⏹️", callback_data="noop"),
        ])
    keyboard.append([InlineKeyboardButton(text="➕ Додати чати", callback_data=f"acc_addchats:{key}")])
    keyboard.append([InlineKeyboardButton(text="⬅️ До акаунта", callback_data=f"acc_view:{key}")])
    text = f"💬 <b>Чати: {escape(_label(key))}</b> ({len(chats)})\n"
    text += "<i>Натисніть на чат, щоб увімкнути/вимкнути, змінити пост або видалити.</i>" if chats \
        else "<i>Список порожній. Натисніть «➕ Додати чати» і надішліть посилання.</i>"
    return text, InlineKeyboardMarkup(inline_keyboard=keyboard)


@router.callback_query(F.data.startswith("acc_chats:"))
async def cb_account_chats(call: CallbackQuery, state: FSMContext):
    if await _deny(call):
        return
    await state.clear()
    _, key, page = call.data.split(":", 2)
    if not _valid_key(key):
        await call.answer("Акаунт не знайдено", show_alert=True)
        return
    text, markup = _chats_view(key, int(page) if page.isdigit() else 0)
    await _edit(call, text, markup)
    await call.answer()


def _chat_view(chat: dict[str, Any]) -> tuple[str, InlineKeyboardMarkup]:
    key = chat.get("account_id") or PRIMARY_ACCOUNT
    posts = {p["id"]: p.get("title", "Пост") for p in db.get_all_posts()}
    text = (
        f"⚙️ <b>Чат</b> <code>{escape(chat['chat_peer'])}</code>\n\n"
        f"• Акаунт: <b>{escape(_label(key))}</b>\n"
        f"• Інтервал: <b>{chat.get('interval_minutes', 60)} хв</b>\n"
        f"• Пост: <b>{escape(posts.get(chat.get('post_id'), '⭐️ За замовчуванням'))}</b>\n"
        f"• Статус: <b>{'увімкнений' if chat.get('is_active') else 'вимкнений'}</b> [{escape(chat.get('status') or '')}]\n"
    )
    if chat.get("last_error"):
        text += f"• Помилка: <i>{escape(chat['last_error'])}</i>\n"
    keyboard = [
        [InlineKeyboardButton(text="⏸️ Вимкнути" if chat.get("is_active") else "▶️ Увімкнути",
                              callback_data=f"acc_ctog:{chat['id']}"),
         InlineKeyboardButton(text="📝 Змінити пост", callback_data=f"chat_manage:{chat['id']}:0")],
        [InlineKeyboardButton(text="🗑️ Видалити чат", callback_data=f"acc_cdel:{chat['id']}")],
        [InlineKeyboardButton(text="⬅️ До чатів акаунта", callback_data=f"acc_chats:{key}:0")],
    ]
    return text, InlineKeyboardMarkup(inline_keyboard=keyboard)


@router.callback_query(F.data.startswith("acc_chat:"))
async def cb_account_chat(call: CallbackQuery):
    if await _deny(call):
        return
    chat = db.get_chat_by_id(call.data.split(":", 1)[1])
    if not chat:
        await call.answer("Чат не знайдено", show_alert=True)
        return
    text, markup = _chat_view(chat)
    await _edit(call, text, markup)
    await call.answer()


@router.callback_query(F.data.startswith("acc_ctog:"))
async def cb_account_chat_toggle(call: CallbackQuery):
    if await _deny(call):
        return
    chat_id = call.data.split(":", 1)[1]
    chat = db.get_chat_by_id(chat_id)
    if not chat:
        await call.answer("Чат не знайдено", show_alert=True)
        return
    enable = not chat.get("is_active")
    updates: dict[str, Any] = {"is_active": enable}
    if enable:
        updates.update(status="active", last_error=None)
    db.update_chat(chat_id, updates)
    text, markup = _chat_view(db.get_chat_by_id(chat_id) or chat)
    await _edit(call, text, markup)
    await call.answer("▶️ Чат увімкнено" if enable else "⏸️ Чат вимкнено")


@router.callback_query(F.data.startswith("acc_cdel:"))
async def cb_account_chat_delete(call: CallbackQuery):
    if await _deny(call):
        return
    chat = db.get_chat_by_id(call.data.split(":", 1)[1])
    if not chat:
        await call.answer("Чат не знайдено", show_alert=True)
        return
    key = chat.get("account_id") or PRIMARY_ACCOUNT
    db.delete_chat(chat["id"])
    text, markup = _chats_view(key, 0)
    await _edit(call, text, markup)
    await call.answer("🗑️ Чат видалено")


# ==================== ADD CHATS TO AN ACCOUNT ====================

@router.callback_query(F.data.startswith("acc_addchats:"))
async def cb_account_add_chats(call: CallbackQuery, state: FSMContext):
    if await _deny(call):
        return
    key = call.data.split(":", 1)[1]
    if not _valid_key(key):
        await call.answer("Акаунт не знайдено", show_alert=True)
        return
    await state.set_state(AccountStates.waiting_for_chat_links)
    await state.update_data(account_key=key)
    text = (
        f"➕ <b>Додавання чатів: {escape(_label(key))}</b>\n\n"
        "Надішліть посилання або юзернейми чатів — по одному в рядок:\n"
        "<code>@chat1\nhttps://t.me/chat2\nhttps://t.me/+invite</code>"
    )
    markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Скасувати", callback_data=f"acc_view:{key}")]])
    await _edit(call, text, markup)
    await call.answer()


@router.message(AccountStates.waiting_for_chat_links, F.text)
async def fsm_account_chat_links(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    peers = parse_chat_links(message.text)
    data = await state.get_data()
    key = data.get("account_key", PRIMARY_ACCOUNT)
    if not peers:
        await message.answer("⚠️ Не знайшов жодного посилання. Надішліть список ще раз:")
        return
    await state.update_data(peers=peers)
    rows = [[InlineKeyboardButton(text=label, callback_data=f"acc_int:{minutes}") for minutes, label in INTERVALS[i:i + 3]]
            for i in range(0, len(INTERVALS), 3)]
    rows.append([InlineKeyboardButton(text="❌ Скасувати", callback_data=f"acc_view:{key}")])
    preview = "\n".join(f"• <code>{escape(p)}</code>" for p in peers[:15])
    more = f"\n<i>...і ще {len(peers) - 15}</i>" if len(peers) > 15 else ""
    await message.answer(
        f"📋 Знайдено чатів: <b>{len(peers)}</b>\n{preview}{more}\n\n⏱️ Оберіть інтервал публікацій:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode="HTML")


@router.callback_query(F.data.startswith("acc_int:"))
async def cb_account_chats_interval(call: CallbackQuery, state: FSMContext):
    if await _deny(call):
        return
    data = await state.get_data()
    if not data.get("peers"):
        await call.answer("Дія застаріла. Почніть додавання знову.", show_alert=True)
        return
    await state.update_data(interval=int(call.data.split(":", 1)[1]))
    rows = [[InlineKeyboardButton(text="⭐️ За замовчуванням (основний)", callback_data="acc_post:default")]]
    for i, p in enumerate(db.get_all_posts(), 1):
        rows.append([InlineKeyboardButton(text=f"{i}. {p.get('title', 'Пост')}"[:60], callback_data=f"acc_post:{p['id']}")])
    await _edit(call, "📝 Оберіть варіант поста для цих чатів (загальний список):",
                InlineKeyboardMarkup(inline_keyboard=rows))
    await call.answer()


@router.callback_query(F.data.startswith("acc_post:"))
async def cb_account_chats_post(call: CallbackQuery, state: FSMContext):
    if await _deny(call):
        return
    data = await state.get_data()
    peers, key = data.get("peers"), data.get("account_key", PRIMARY_ACCOUNT)
    if not peers:
        await call.answer("Дія застаріла. Почніть додавання знову.", show_alert=True)
        return
    post_value = call.data.split(":", 1)[1]
    post_id: Optional[str] = None if post_value == "default" else post_value
    interval = data.get("interval", 1440)
    await state.clear()
    await call.answer("⏳ Додаємо чати...")
    added, conflicts, failed = 0, [], 0
    for peer in peers:
        try:
            if db.add_chat(peer, interval, post_id=post_id, account=key):
                added += 1
            else:
                failed += 1
        except ChatConflictError as e:
            conflicts.append(str(e))
    report = f"✅ Додано чатів: <b>{added}</b> з {len(peers)} у «{escape(_label(key))}»."
    if conflicts:
        report += "\n\n⚠️ Пропущено (вже в іншого акаунта):\n" + "\n".join(f"• {escape(c)}" for c in conflicts[:10])
    if failed:
        report += f"\n\n❌ Не вдалося зберегти: {failed}"
    if key != PRIMARY_ACCOUNT and not (db.get_account(key) or {}).get("posting_enabled"):
        report += "\n\n💡 Розсилка з цього акаунта вимкнена — увімкніть її в картці акаунта."
    text, markup = _chats_view(key, 0)
    await _edit(call, report + "\n\n" + text, markup)


# ==================== ADD ACCOUNT (PHONE LOGIN) ====================

@router.callback_query(F.data == "acc_add")
async def cb_account_add(call: CallbackQuery, state: FSMContext):
    if await _deny(call):
        return
    if not db.supports_accounts():
        await call.answer("Спочатку виконайте міграцію бази даних для акаунтів.", show_alert=True)
        return
    await state.set_state(AccountStates.waiting_for_phone)
    text = (
        "➕ <b>Новий акаунт для розсилки</b> (Крок 1/3)\n\n"
        "Введіть номер телефону акаунта у міжнародному форматі.\n"
        "Наприклад: <code>+380991234567</code>\n\n"
        "<i>Також можна додати акаунт через QR-код у веб-панелі.</i>"
    )
    await _edit(call, text, _cancel_login_kb())
    await call.answer()


@router.message(AccountStates.waiting_for_phone, F.text)
async def fsm_account_phone(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    phone = normalize_phone(message.text)
    if not phone or len(phone) < 8:
        await message.answer("⚠️ Введіть коректний номер (наприклад <code>+380991234567</code>):",
                             reply_markup=_cancel_login_kb(), parse_mode="HTML")
        return
    wait = await message.answer("⏳ <i>Надсилаємо запит коду в Telegram...</i>", parse_mode="HTML")
    try:
        login = await account_logins.start_phone(phone)
    except AccountLoginError as e:
        await wait.edit_text(f"❌ {escape(str(e))}\n\nВведіть номер ще раз:", reply_markup=_cancel_login_kb(),
                             parse_mode="HTML")
        return
    await state.update_data(login_id=login.id)
    await state.set_state(AccountStates.waiting_for_code)
    await wait.edit_text(
        f"📩 <b>Код надіслано</b> (Крок 2/3)\n\n"
        f"📱 Номер: <code>{escape(phone)}</code>\n"
        f"📍 Куди: {describe_sent_code_type(login.sent_code_type)}\n\n"
        "⚠️ <b>Важливо:</b> введіть код <u>з дефісами або пробілами</u>, наприклад <code>1-2-3-4-5</code>. "
        "Якщо надіслати код суцільно, Telegram може його анулювати, бо його переслали в іншому чаті.",
        reply_markup=_cancel_login_kb(), parse_mode="HTML")


async def _login_done(message: Message, state: FSMContext, login_id: str) -> None:
    await state.clear()
    login = account_logins.get(login_id)
    account = login.account or {}
    await account_logins.cancel(login_id)
    name = escape((login.user or {}).get("name") or account.get("label") or "")
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Додати чати", callback_data=f"acc_addchats:{account.get('id')}")],
        [InlineKeyboardButton(text="👤 Картка акаунта", callback_data=f"acc_view:{account.get('id')}")],
        [InlineKeyboardButton(text="⬅️ До акаунтів", callback_data="acc_list")],
    ])
    await message.answer(
        f"🎉 <b>Акаунт {name} додано!</b>\n\n"
        "Далі:\n1. Додайте чати для цього акаунта.\n2. Увімкніть розсилку в картці акаунта.\n\n"
        "<i>Розсилка з нового акаунта за замовчуванням вимкнена.</i>",
        reply_markup=markup, parse_mode="HTML")


@router.message(AccountStates.waiting_for_code, F.text)
async def fsm_account_code(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    login_id = (await state.get_data()).get("login_id")
    wait = await message.answer("⏳ <i>Перевіряємо код...</i>", parse_mode="HTML")
    try:
        login = await account_logins.submit_code(login_id, message.text)
    except AccountLoginError as e:
        await wait.edit_text(f"❌ {escape(str(e))}", reply_markup=_cancel_login_kb(), parse_mode="HTML")
        return
    await wait.delete()
    if login.status == "needs_2fa":
        await state.set_state(AccountStates.waiting_for_2fa)
        await message.answer("🔐 <b>Двоетапна перевірка</b> (Крок 3/3)\n\nВведіть хмарний пароль 2FA цього акаунта:",
                             reply_markup=_cancel_login_kb(), parse_mode="HTML")
        return
    await _login_done(message, state, login_id)


@router.message(AccountStates.waiting_for_2fa, F.text)
async def fsm_account_2fa(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    password = message.text
    try:
        await message.delete()
    except Exception:
        pass
    login_id = (await state.get_data()).get("login_id")
    wait = await message.answer("⏳ <i>Перевіряємо хмарний пароль...</i>", parse_mode="HTML")
    try:
        await account_logins.submit_password(login_id, password)
    except AccountLoginError as e:
        await wait.edit_text(f"❌ {escape(str(e))}", reply_markup=_cancel_login_kb(), parse_mode="HTML")
        return
    await wait.delete()
    await _login_done(message, state, login_id)


@router.callback_query(F.data == "acc_login_cancel")
async def cb_account_login_cancel(call: CallbackQuery, state: FSMContext):
    if await _deny(call):
        return
    login_id = (await state.get_data()).get("login_id")
    if login_id:
        await account_logins.cancel(login_id)
    await state.clear()
    text, markup = _accounts_view()
    await _edit(call, text, markup)
    await call.answer("Додавання акаунта скасовано")
