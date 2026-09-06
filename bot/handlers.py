import uuid
import logging
from datetime import datetime, timezone
from typing import Dict, Any

from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from config import config
from database.client import db
from bot.keyboards import (
    get_main_menu_keyboard,
    get_posts_list_keyboard,
    get_post_detail_keyboard,
    get_post_save_action_keyboard,
    get_chats_list_keyboard,
    get_chat_post_select_keyboard,
    get_post_assign_menu_keyboard,
    get_back_keyboard,
    get_back_to_posts_keyboard,
    get_cb_action
)

logger = logging.getLogger("AdminBot")
router = Router()

# In-memory storage for unconfirmed received messages
_temp_messages: Dict[str, Dict[str, Any]] = {}


class PostStates(StatesGroup):
    waiting_for_new_post_content = State()
    waiting_for_new_post_title = State()
    waiting_for_edit_content = State()
    waiting_for_rename_title = State()


def is_admin(user_id: int) -> bool:
    admin_ids = config.get_admin_ids()
    if not admin_ids:
        return True
    return user_id in admin_ids


def get_status_text() -> str:
    settings = db.get_settings()
    is_running = settings.get("is_running", False)
    chats = db.get_all_chats()
    posts = db.get_all_posts()
    active_chats = sum(1 for c in chats if c.get("is_active"))
    error_chats = sum(1 for c in chats if c.get("status") in ["error", "restricted"])

    status_emoji = "🟢 АКТИВНИЙ" if is_running else "⏸️ НА ПАУЗІ"

    text = (
        f"🚀 <b>Панель керування автопостером</b>\n\n"
        f"• Статус розсилки: <b>{status_emoji}</b>\n"
        f"• Всього чатів: <b>{len(chats)}</b> (активних: {active_chats})\n"
        f"• Варіантів постів: <b>{len(posts)}</b>\n"
        f"• Помилок / обмежень: <b>{error_chats}</b>\n"
        f"• Затримка між чатами: <b>{settings.get('min_delay_seconds', 15)}-{settings.get('max_delay_seconds', 35)}с</b>\n"
        f"• Джиттер: <b>±{settings.get('jitter_minutes', 3)} хв</b>\n"
    )
    return text


# ==================== MAIN MENU & BASIC COMMANDS ====================

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("🚫 Доступ заборонено.")
        return

    await state.clear()
    settings = db.get_settings()
    is_running = settings.get("is_running", False)
    await message.answer(
        get_status_text(),
        reply_markup=get_main_menu_keyboard(is_running),
        parse_mode="HTML"
    )


@router.callback_query(F.data == "btn_refresh")
async def cb_refresh(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("🚫 Доступ заборонено.", show_alert=True)
        return

    await state.clear()
    settings = db.get_settings()
    is_running = settings.get("is_running", False)
    try:
        await call.message.edit_text(
            get_status_text(),
            reply_markup=get_main_menu_keyboard(is_running),
            parse_mode="HTML"
        )
    except Exception:
        await call.message.answer(
            get_status_text(),
            reply_markup=get_main_menu_keyboard(is_running),
            parse_mode="HTML"
        )
    await call.answer("Оновлено ✅")


@router.callback_query(F.data == "btn_pause")
async def cb_pause(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("🚫 Доступ заборонено.", show_alert=True)
        return

    db.toggle_poster(False)
    await call.message.edit_text(
        get_status_text(),
        reply_markup=get_main_menu_keyboard(False),
        parse_mode="HTML"
    )
    await call.answer("⏸️ Розсилку призупинено!", show_alert=True)


@router.callback_query(F.data == "btn_resume")
async def cb_resume(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("🚫 Доступ заборонено.", show_alert=True)
        return

    db.toggle_poster(True)
    await call.message.edit_text(
        get_status_text(),
        reply_markup=get_main_menu_keyboard(True),
        parse_mode="HTML"
    )
    await call.answer("▶️ Розсилку успішно запущено!", show_alert=True)


@router.callback_query(F.data == "btn_status")
async def cb_status_details(call: CallbackQuery):
    chats = db.get_all_chats()
    posts = {p["id"]: p.get("title", "Пост") for p in db.get_all_posts()}
    now = datetime.now(timezone.utc)

    # Sort chats by nearest next_post_at
    def get_sort_key(c):
        np = c.get("next_post_at")
        if not np:
            return "9999"
        return np

    sorted_chats = sorted(chats, key=get_sort_key)

    lines = [f"📊 <b>Статус публікацій ({len(chats)} чатів):</b>\n"]
    for chat in sorted_chats[:25]:
        peer = chat["chat_peer"]
        interval = chat.get("interval_minutes", 60)
        status = chat.get("status", "active")
        next_post = chat.get("next_post_at")
        post_title = posts.get(chat.get("post_id"), "⭐️ Дефолт")
        
        if next_post:
            try:
                next_dt = datetime.fromisoformat(next_post)
                diff_m = int((next_dt - now).total_seconds() / 60)
                diff_str = f"через {diff_m}хв" if diff_m > 0 else "готовий зараз ⚡"
            except Exception:
                diff_str = "—"
        else:
            diff_str = "—"

        lines.append(f"• <code>{peer}</code> ({interval}хв): {diff_str}\n  └ <i>Шаблон: {post_title}</i> [{status}]")

    if not chats:
        lines.append("<i>Список чатів порожній.</i>")
    elif len(chats) > 25:
        lines.append(f"\n<i>...і ще {len(chats) - 25} чатів.</i>")

    await call.message.edit_text("\n".join(lines), reply_markup=get_back_keyboard(), parse_mode="HTML")
    await call.answer()


# ==================== POSTS MANAGEMENT ====================

@router.message(Command("posts"))
@router.callback_query(F.data == "btn_posts_list")
async def cb_posts_list(event: Message | CallbackQuery, state: FSMContext):
    user_id = event.from_user.id
    if not is_admin(user_id):
        if isinstance(event, CallbackQuery):
            await event.answer("🚫 Доступ заборонено.", show_alert=True)
        return

    await state.clear()
    posts = db.get_all_posts()
    
    text = (
        f"📝 <b>Керування варіантами постів ({len(posts)}):</b>\n\n"
        f"Ви можете створювати окремі тексти під різні типи пабліків. "
        f"Пост, помічений ⭐️, є <b>основним за замовчуванням</b> для всіх чатів без індивідуального призначення.\n\n"
        f"👇 <i>Оберіть варіант для перегляду та налаштувань:</i>"
    )
    
    markup = get_posts_list_keyboard(posts)
    
    if isinstance(event, CallbackQuery):
        await event.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
        await event.answer()
    else:
        await event.answer(text, reply_markup=markup, parse_mode="HTML")


@router.callback_query(F.data.startswith("post_view:"))
async def cb_post_view(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("🚫 Доступ заборонено.", show_alert=True)
        return

    post_id = call.data.split(":", 1)[1]
    post = db.get_post_by_id(post_id)
    if not post:
        await call.answer("⚠️ Пост не знайдено!", show_alert=True)
        return

    is_default = post.get("is_active", False)
    assigned_count = post.get("assigned_chats_count", 0)
    
    # Calculate assigned chats if not present
    if "assigned_chats_count" not in post:
        chats = db.get_all_chats()
        assigned_count = sum(1 for c in chats if c.get("post_id") == post_id)

    status_tag = "⭐️ <b>ОСНОВНИЙ (ЗА ЗАМОВЧУВАННЯМ)</b>" if is_default else "🔹 Окремий варіант"
    src_info = f"\n• Клон Telegram-повідомлення: <b>#{post.get('source_msg_id')}</b>" if post.get("source_msg_id") else ""

    text = (
        f"📝 <b>Шаблон: {post.get('title', 'Без назви')}</b>\n\n"
        f"• Статус: {status_tag}\n"
        f"• Прив'язано чатів: <b>{assigned_count}</b>{src_info}\n\n"
        f"<b>📄 Текст оголошення:</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{post.get('content', '')}\n"
        f"━━━━━━━━━━━━━━━━━━━━"
    )

    markup = get_post_detail_keyboard(post)
    try:
        await call.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    except Exception:
        fallback_text = (
            f"📝 Шаблон: {post.get('title', 'Без назви')}\n\n"
            f"• Статус: {status_tag}\n"
            f"• Прив'язано чатів: {assigned_count}\n\n"
            f"Текст:\n{post.get('content', '')}"
        )
        await call.message.edit_text(fallback_text, reply_markup=markup)
    await call.answer()


@router.callback_query(F.data.startswith("post_set_default:"))
async def cb_post_set_default(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("🚫 Доступ заборонено.", show_alert=True)
        return

    post_id = call.data.split(":", 1)[1]
    db.set_default_post(post_id)
    await call.answer("⭐️ Встановлено як основне дефолтне оголошення!", show_alert=True)
    
    # Refresh view
    post = db.get_post_by_id(post_id)
    if post:
        await cb_post_view(call)


@router.callback_query(F.data.startswith("post_delete:"))
async def cb_post_delete(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("🚫 Доступ заборонено.", show_alert=True)
        return

    post_id = call.data.split(":", 1)[1]
    all_posts = db.get_all_posts()
    if len(all_posts) <= 1:
        await call.answer("⚠️ Не можна видалити єдине оголошення в системі!", show_alert=True)
        return

    db.delete_post(post_id)
    await call.answer("🗑️ Варіант оголошення успішно видалено!", show_alert=True)
    await cb_posts_list(call, None)


# ==================== ASSIGN POST TO CHATS ====================

@router.callback_query(F.data.startswith("post_assign_menu:"))
async def cb_post_assign_menu(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("🚫 Доступ заборонено.", show_alert=True)
        return

    post_id = call.data.split(":", 1)[1]
    post = db.get_post_by_id(post_id)
    if not post:
        await call.answer("Пост не знайдено", show_alert=True)
        return

    chats = db.get_all_chats()
    assigned_count = sum(1 for c in chats if c.get("post_id") == post_id)
    
    text = (
        f"👥 <b>Призначення варіанту «{post.get('title', '')}» на чати:</b>\n\n"
        f"• Зараз закріплено за: <b>{assigned_count} із {len(chats)} чатів</b>\n\n"
        f"💡 <i>Натискайте на кнопки чатів нижче, щоб увімкнути (✅) або вимкнути (➕) цей пост для конкретного пабліка:</i>"
    )
    markup = get_post_assign_menu_keyboard(post_id, chats)
    try:
        await call.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    except Exception:
        pass
    await call.answer()


@router.callback_query(F.data.startswith("post_assign_all:"))
async def cb_post_assign_all(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("🚫 Доступ заборонено.", show_alert=True)
        return

    post_id = call.data.split(":", 1)[1]
    chats = db.get_all_chats()
    for c in chats:
        db.update_chat(c["id"], {"post_id": post_id})

    await call.answer("⚡ Успішно призначено на всі чати!", show_alert=True)
    await cb_post_assign_menu(call)


@router.callback_query(F.data.startswith("post_unassign_all:"))
async def cb_post_unassign_all(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("🚫 Доступ заборонено.", show_alert=True)
        return

    post_id = call.data.split(":", 1)[1]
    chats = db.get_all_chats()
    for c in chats:
        if c.get("post_id") == post_id:
            db.update_chat(c["id"], {"post_id": None})

    await call.answer("🔄 Всі чати переведено на дефолтний пост!", show_alert=True)
    await cb_post_assign_menu(call)


@router.callback_query(F.data.startswith("act:"))
async def cb_action_dispatcher(call: CallbackQuery):
    """Unified token-based dispatcher to keep callback_data well under 64 bytes."""
    if not is_admin(call.from_user.id):
        await call.answer("🚫 Доступ заборонено.", show_alert=True)
        return

    token = call.data.split(":", 1)[1]
    action_data = get_cb_action(token)
    if not action_data:
        await call.answer("⚠️ Дія застаріла. Оновіть меню.", show_alert=True)
        return

    act_type = action_data.get("type")

    if act_type == "set_chat_post":
        chat_id = action_data.get("chat_id")
        post_id = action_data.get("post_id")
        page = action_data.get("page", 0)
        ok = db.update_chat(chat_id, {"post_id": post_id})
        if ok:
            await call.answer("✅ Варіант поста для чату успішно змінено!", show_alert=True)
        else:
            await call.answer("⚠️ Не вдалося зберегти.", show_alert=True)
        
        # Render the specific return page
        chats = db.get_all_chats()
        posts = {p["id"]: p.get("title", "Пост") for p in db.get_all_posts()}
        total_chats = len(chats)
        page_size = 10
        total_pages = max(1, (total_chats + page_size - 1) // page_size)
        page = max(0, min(page, total_pages - 1))
        text = (
            f"💬 <b>Підключені чати ({total_chats}) • Стор. {page + 1}/{total_pages}:</b>\n"
            f"<i>Натисніть на кнопку чату нижче, щоб змінити закріплений варіант поста:</i>\n\n"
        )
        markup = get_chats_list_keyboard(chats, posts, page=page, page_size=page_size)
        await call.message.edit_text(text, reply_markup=markup, parse_mode="HTML")

    elif act_type == "post_toggle_chat":
        post_id = action_data.get("post_id")
        chat_id = action_data.get("chat_id")
        action = action_data.get("action")
        page = action_data.get("page", 0)

        new_post_id = post_id if action == "assign" else None
        ok = db.update_chat(chat_id, {"post_id": new_post_id})

        if ok:
            status_msg = "✅ Прикріплено" if action == "assign" else "Скинуто на дефолт"
            await call.answer(status_msg)
        else:
            await call.answer("⚠️ Помилка оновлення.", show_alert=True)

        # Refresh assignment menu with pagination
        chats = db.get_all_chats()
        post = db.get_post_by_id(post_id)
        if post:
            assigned_count = sum(1 for c in chats if c.get("post_id") == post_id)
            total_pages = max(1, (len(chats) + 9) // 10)
            text = (
                f"👥 <b>Призначення варіанту «{post.get('title', '')}» на чати:</b>\n\n"
                f"• Зараз закріплено за: <b>{assigned_count} із {len(chats)} чатів</b>\n"
                f"• Сторінка: <b>{page + 1} із {total_pages}</b>\n\n"
                f"💡 <i>Натискайте на кнопки чатів нижче, щоб увімкнути (✅) або вимкнути (➕) цей пост для конкретного пабліка:</i>"
            )
            markup = get_post_assign_menu_keyboard(post_id, chats, page=page)
            try:
                await call.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
            except Exception:
                pass


# ==================== POST CREATION & EDITING VIA FSM ====================

@router.callback_query(F.data == "post_add_new")
async def cb_post_add_new(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("🚫 Доступ заборонено.", show_alert=True)
        return

    await state.set_state(PostStates.waiting_for_new_post_content)
    text = (
        "➕ <b>Створення нового варіанту поста:</b>\n\n"
        "1. Надішліть сюди новий текст з HTML-тегами або перешліть (forward) готове повідомлення з каналу.\n"
        "2. Також підтримується Spintax <code>{варіант 1|варіант 2}</code>.\n\n"
        "<i>Надішліть повідомлення або натисніть «Скасувати»:</i>"
    )
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Скасувати", callback_data="btn_posts_list")]
    ])
    await call.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    await call.answer()


@router.message(PostStates.waiting_for_new_post_content, F.text | F.caption)
async def fsm_receive_new_post_content(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    html_content = message.html_text or message.caption or message.text or ""
    await state.update_data(new_content=html_content)
    await state.set_state(PostStates.waiting_for_new_post_title)

    existing_count = len(db.get_all_posts())
    default_suggested_title = f"Оголошення #{existing_count + 1}"

    text = (
        f"✅ <b>Текст отримано!</b>\n\n"
        f"Тепер введіть <b>назву для цього варіанту</b> (наприклад: <i>«Для крипто-чатів»</i>, <i>«Товари Київ»</i>):\n\n"
        f"<i>Або надішліть <code>-</code> щоб залишити назву: «{default_suggested_title}».</i>"
    )
    await message.answer(text, parse_mode="HTML")


@router.message(PostStates.waiting_for_new_post_title, F.text)
async def fsm_save_new_post_title(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    data = await state.get_data()
    content = data.get("new_content", "")
    title_input = message.text.strip()

    existing_count = len(db.get_all_posts())
    title = f"Оголошення #{existing_count + 1}" if title_input == "-" else title_input

    new_post = db.create_post(
        content=content,
        title=title,
        is_active=False
    )
    await state.clear()

    if new_post:
        await message.answer(
            f"🎉 <b>Новий варіант «{title}» успішно створено!</b>\n\n"
            f"Ви можете призначити його на потрібні чати у вкладці чатів.",
            reply_markup=get_posts_list_keyboard(db.get_all_posts()),
            parse_mode="HTML"
        )
    else:
        await message.answer("⚠️ Помилка збереження поста.")


@router.callback_query(F.data.startswith("post_edit_text:"))
async def cb_post_edit_text(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("🚫 Доступ заборонено.", show_alert=True)
        return

    post_id = call.data.split(":", 1)[1]
    post = db.get_post_by_id(post_id)
    if not post:
        await call.answer("Пост не знайдено", show_alert=True)
        return

    await state.set_state(PostStates.waiting_for_edit_content)
    await state.update_data(edit_post_id=post_id)

    text = (
        f"✏️ <b>Редагування тексту «{post.get('title', '')}»:</b>\n\n"
        f"Надішліть сюди новий текст або перешліть (forward) нове повідомлення."
    )
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Скасувати", callback_data=f"post_view:{post_id}")]
    ])
    await call.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    await call.answer()


@router.message(PostStates.waiting_for_edit_content, F.text | F.caption)
async def fsm_save_edited_content(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    data = await state.get_data()
    post_id = data.get("edit_post_id")
    html_content = message.html_text or message.caption or message.text or ""

    if post_id:
        db.update_post(post_id, {"content": html_content, "source_msg_id": None})
        await state.clear()
        await message.answer(
            f"✅ <b>Текст оголошення оновлено!</b>",
            reply_markup=get_posts_list_keyboard(db.get_all_posts()),
            parse_mode="HTML"
        )


@router.callback_query(F.data.startswith("post_rename:"))
async def cb_post_rename(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("🚫 Доступ заборонено.", show_alert=True)
        return

    post_id = call.data.split(":", 1)[1]
    post = db.get_post_by_id(post_id)
    if not post:
        await call.answer("Пост не знайдено", show_alert=True)
        return

    await state.set_state(PostStates.waiting_for_rename_title)
    await state.update_data(rename_post_id=post_id)

    text = (
        f"🏷️ <b>Перейменування «{post.get('title', '')}»:</b>\n\n"
        f"Введіть нову назву для цього шаблону:"
    )
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Скасувати", callback_data=f"post_view:{post_id}")]
    ])
    await call.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    await call.answer()


@router.message(PostStates.waiting_for_rename_title, F.text)
async def fsm_save_rename_title(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    data = await state.get_data()
    post_id = data.get("rename_post_id")
    new_title = message.text.strip()

    if post_id and new_title:
        db.update_post(post_id, {"title": new_title})
        await state.clear()
        await message.answer(
            f"✅ <b>Назву змінено на «{new_title}»!</b>",
            reply_markup=get_posts_list_keyboard(db.get_all_posts()),
            parse_mode="HTML"
        )


# ==================== SYNC SAVED MESSAGES (MTProto) ====================

@router.callback_query(F.data == "btn_sync_saved")
async def cb_sync_saved(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("🚫 Доступ заборонено.", show_alert=True)
        return

    from core.client import get_latest_saved_message
    res = await get_latest_saved_message()
    if res.get("status") != "ok":
        await call.answer(f"⚠️ {res.get('message', 'Помилка')}", show_alert=True)
        return

    msg_id = res.get("message_id")
    html_text = res.get("html_text") or res.get("raw_text") or ""
    raw_text = res.get("raw_text") or html_text

    posts = db.get_all_posts()
    title = f"Зі Збережених #{msg_id}"

    saved_post = db.create_post(
        content=html_text,
        title=title,
        source_msg_id=msg_id,
        source_chat_peer="me",
        is_active=False
    )

    emoji_status = "ТАК (анімовані) ⭐" if res.get("has_custom_emojis") else "Звичайні"
    text = (
        f"🎉 <b>Створено новий варіант «{title}»!</b>\n\n"
        f"• Premium Емодзі: <b>{emoji_status}</b>\n"
        f"• Кількість сутностей: <b>{res.get('entities_count', 0)}</b>\n\n"
        f"<b>Попередній перегляд:</b>\n{html_text}"
    )

    try:
        await call.message.edit_text(text, reply_markup=get_posts_list_keyboard(db.get_all_posts()), parse_mode="HTML")
    except Exception:
        fallback_text = (
            f"🎉 Створено новий варіант «{title}»!\n\n"
            f"• Premium Емодзі: {emoji_status}\n\n"
            f"Попередній перегляд:\n{raw_text}"
        )
        await call.message.edit_text(fallback_text, reply_markup=get_posts_list_keyboard(db.get_all_posts()))

    await call.answer("Синхронізовано з Telegram! ✅")


# ==================== CHAT POST ASSIGNMENT ====================

@router.callback_query(F.data == "noop")
async def cb_noop(call: CallbackQuery):
    await call.answer()


@router.callback_query(F.data.startswith("chats_page:") | (F.data == "btn_chats"))
async def cb_chats_list(call: CallbackQuery):
    page = 0
    if call.data.startswith("chats_page:"):
        try:
            page = int(call.data.split(":", 1)[1])
        except Exception:
            page = 0

    chats = db.get_all_chats()
    posts = {p["id"]: p.get("title", "Пост") for p in db.get_all_posts()}

    if not chats:
        text = "💬 <b>Список чатів порожній.</b>\nДодайте їх через Desktop UI на ПК."
        await call.message.edit_text(text, reply_markup=get_back_keyboard(), parse_mode="HTML")
    else:
        total_chats = len(chats)
        page_size = 10
        total_pages = max(1, (total_chats + page_size - 1) // page_size)
        page = max(0, min(page, total_pages - 1))

        text = (
            f"💬 <b>Підключені чати ({total_chats}) • Стор. {page + 1}/{total_pages}:</b>\n"
            f"<i>Натисніть на кнопку чату нижче, щоб змінити закріплений варіант поста:</i>\n\n"
        )
        markup = get_chats_list_keyboard(chats, posts, page=page, page_size=page_size)
        await call.message.edit_text(text, reply_markup=markup, parse_mode="HTML")

    await call.answer()


@router.callback_query(F.data.startswith("chat_manage:"))
async def cb_chat_manage(call: CallbackQuery):
    parts = call.data.split(":")
    chat_id = parts[1]
    page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0

    chats = db.get_all_chats()
    chat = next((c for c in chats if c["id"] == chat_id), None)
    if not chat:
        await call.answer("Чат не знайдено", show_alert=True)
        return

    posts = db.get_all_posts()
    curr_post_id = chat.get("post_id")
    post_name = next((p["title"] for p in posts if p["id"] == curr_post_id), "⭐️ За замовчуванням")

    text = (
        f"⚙️ <b>Налаштування чату:</b> <code>{chat['chat_peer']}</code>\n\n"
        f"• Інтервал: <b>{chat.get('interval_minutes', 60)} хв</b>\n"
        f"• Поточний варіант поста: <b>{post_name}</b>\n\n"
        f"👇 <i>Оберіть варіант оголошення, який буде публікуватися в цей чат:</i>"
    )
    markup = get_chat_post_select_keyboard(chat_id, posts, curr_post_id, return_page=page)
    await call.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    await call.answer()


@router.callback_query(F.data.startswith("assign_page:"))
async def cb_assign_page(call: CallbackQuery):
    parts = call.data.split(":")
    post_id = parts[1]
    page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0

    chats = db.get_all_chats()
    post = db.get_post_by_id(post_id)
    if not post:
        await call.answer("Пост не знайдено", show_alert=True)
        return

    assigned_count = sum(1 for c in chats if c.get("post_id") == post_id)
    total_pages = max(1, (len(chats) + 9) // 10)
    text = (
        f"👥 <b>Призначення варіанту «{post.get('title', '')}» на чати:</b>\n\n"
        f"• Зараз закріплено за: <b>{assigned_count} із {len(chats)} чатів</b>\n"
        f"• Сторінка: <b>{page + 1} із {total_pages}</b>\n\n"
        f"💡 <i>Натискайте на кнопки чатів нижче, щоб увімкнути (✅) або вимкнути (➕) цей пост для конкретного пабліка:</i>"
    )
    markup = get_post_assign_menu_keyboard(post_id, chats, page=page)
    await call.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    await call.answer()


@router.callback_query(F.data.startswith("set_chat_post:"))
async def cb_set_chat_post(call: CallbackQuery):
    parts = call.data.split(":")
    chat_id = parts[1]
    target_post_id = None if parts[2] == "default" else parts[2]

    db.update_chat(chat_id, {"post_id": target_post_id})
    await call.answer("✅ Варіант поста для чату успішно змінено!", show_alert=True)
    await cb_chats_list(call)


# ==================== DIRECT MESSAGE / FORWARD HANDLING ====================

@router.message(Command("addpost"))
async def cmd_add_post(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await cb_post_add_new(message, state)


@router.message(Command("setpost"))
@router.message(Command("post"))
async def cmd_set_post_guide(message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer(
        "📝 <b>Як керувати варіантами оголошень:</b>\n\n"
        "1. Перейдіть у меню <b>«📝 Варіанти постів»</b> для перегляду всіх створених шаблонів.\n"
        "2. Щоб створити новий — просто <b>надішліть або перешліть сюди будь-яке повідомлення</b>.\n"
        "3. Бот запропонує зберегти його як новий варіант або оновити основний.\n"
        "4. Ви можете закріпити різні варіанти за різними групами!",
        parse_mode="HTML"
    )


@router.message(F.text | F.caption)
async def handle_incoming_unprompted_message(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    # Check if we are currently in an FSM state
    cur_state = await state.get_state()
    if cur_state is not None:
        return

    if message.text and message.text.startswith("/"):
        return

    html_content = message.html_text or message.caption or message.text or ""
    raw_content = message.text or message.caption or ""

    temp_key = str(uuid.uuid4())[:8]
    _temp_messages[temp_key] = {
        "content": html_content,
        "raw": raw_content,
        "time": datetime.now()
    }

    text = (
        f"📥 <b>Отримано нове повідомлення для розсилки!</b>\n\n"
        f"<b>Текст:</b>\n{html_content}\n\n"
        f"👇 <i>Оберіть, як зберегти це оголошення:</i>"
    )

    markup = get_post_save_action_keyboard(temp_key)
    try:
        await message.answer(text, reply_markup=markup, parse_mode="HTML")
    except Exception:
        await message.answer(
            f"📥 Отримано нове повідомлення!\n\n{raw_content}\n\nОберіть дію:",
            reply_markup=markup
        )


@router.callback_query(F.data.startswith("save_as_new:"))
async def cb_action_save_as_new(call: CallbackQuery):
    temp_key = call.data.split(":", 1)[1]
    temp_data = _temp_messages.pop(temp_key, None)
    if not temp_data:
        await call.answer("Час дії повідомлення вичерпано", show_alert=True)
        return

    existing_count = len(db.get_all_posts())
    title = f"Оголошення #{existing_count + 1}"
    db.create_post(
        content=temp_data["content"],
        title=title,
        is_active=False
    )
    await call.message.edit_text(
        f"🎉 <b>Створено новий варіант: «{title}»!</b>\n\n"
        f"Ви можете переглянути його в меню постів або призначити на обрані чати.",
        reply_markup=get_posts_list_keyboard(db.get_all_posts()),
        parse_mode="HTML"
    )
    await call.answer("Збережено як новий варіант! ✅")


@router.callback_query(F.data.startswith("save_as_default:"))
async def cb_action_save_as_default(call: CallbackQuery):
    temp_key = call.data.split(":", 1)[1]
    temp_data = _temp_messages.pop(temp_key, None)
    if not temp_data:
        await call.answer("Час дії повідомлення вичерпано", show_alert=True)
        return

    active_post = db.get_active_post()
    if active_post:
        db.update_post(active_post["id"], {"content": temp_data["content"], "source_msg_id": None})
    else:
        db.create_post(content=temp_data["content"], title="Головне оголошення", is_active=True)

    await call.message.edit_text(
        f"✅ <b>Основне оголошення за замовчуванням успішно оновлено!</b>",
        reply_markup=get_main_menu_keyboard(db.get_settings().get("is_running", False)),
        parse_mode="HTML"
    )
    await call.answer("Основний пост оновлено! ✅")


@router.callback_query(F.data == "btn_cancel_action")
async def cb_cancel_action(call: CallbackQuery):
    await call.message.edit_text(
        "❌ Дію скасовано.",
        reply_markup=get_main_menu_keyboard(db.get_settings().get("is_running", False))
    )
    await call.answer()
