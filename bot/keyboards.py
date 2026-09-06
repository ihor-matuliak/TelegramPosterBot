from typing import List, Dict, Any, Optional
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def get_main_menu_keyboard(is_running: bool) -> InlineKeyboardMarkup:
    """Generate main admin menu keyboard."""
    toggle_text = "⏸️ Поставити на паузу" if is_running else "▶️ Запустити розсилку"
    toggle_cb = "btn_pause" if is_running else "btn_resume"

    keyboard = [
        [
            InlineKeyboardButton(text=toggle_text, callback_data=toggle_cb),
            InlineKeyboardButton(text="📊 Статус і таймери", callback_data="btn_status")
        ],
        [
            InlineKeyboardButton(text="💬 Список чатів", callback_data="btn_chats"),
            InlineKeyboardButton(text="📝 Варіанти постів", callback_data="btn_posts_list")
        ],
        [
            InlineKeyboardButton(text="⭐ 📥 Завантажити зі «Збережених»", callback_data="btn_sync_saved")
        ],
        [
            InlineKeyboardButton(text="🔄 Оновити меню", callback_data="btn_refresh")
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_posts_list_keyboard(posts: List[Dict[str, Any]]) -> InlineKeyboardMarkup:
    """Keyboard displaying all available post variants."""
    keyboard = []
    
    for i, p in enumerate(posts, 1):
        pid = p["id"]
        title = p.get("title", f"Варіант #{i}")
        is_default = p.get("is_active", False)
        badge = "⭐️ " if is_default else ""
        assigned_count = p.get("assigned_chats_count", 0)
        count_str = f" ({assigned_count} чатів)" if assigned_count > 0 else ""
        
        btn_text = f"{badge}{i}. {title}{count_str}"
        if len(btn_text) > 40:
            btn_text = btn_text[:37] + "..."
            
        keyboard.append([InlineKeyboardButton(text=btn_text, callback_data=f"post_view:{pid}")])

    # Action buttons
    keyboard.append([
        InlineKeyboardButton(text="➕ Створити новий варіант", callback_data="post_add_new"),
        InlineKeyboardButton(text="⭐ 📥 Зі Збережених", callback_data="btn_sync_saved")
    ])
    keyboard.append([
        InlineKeyboardButton(text="⬅️ Головне меню", callback_data="btn_refresh")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_post_detail_keyboard(post: Dict[str, Any]) -> InlineKeyboardMarkup:
    """Keyboard for single post management."""
    pid = post["id"]
    is_default = post.get("is_active", False)
    
    keyboard = []
    
    if not is_default:
        keyboard.append([
            InlineKeyboardButton(text="⭐️ Зробити основним (Default)", callback_data=f"post_set_default:{pid}")
        ])

    keyboard.append([
        InlineKeyboardButton(text="✏️ Змінити текст", callback_data=f"post_edit_text:{pid}"),
        InlineKeyboardButton(text="🏷️ Перейменувати", callback_data=f"post_rename:{pid}")
    ])
    keyboard.append([
        InlineKeyboardButton(text="👥 Призначити на чати", callback_data=f"post_assign_menu:{pid}"),
        InlineKeyboardButton(text="🗑️ Видалити варіант", callback_data=f"post_delete:{pid}")
    ])
    keyboard.append([
        InlineKeyboardButton(text="⬅️ До списку варіантів", callback_data="btn_posts_list"),
        InlineKeyboardButton(text="🏠 Головне меню", callback_data="btn_refresh")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_post_save_action_keyboard(temp_key: str) -> InlineKeyboardMarkup:
    """Interactive decision keyboard when admin sends a raw text or forward message."""
    keyboard = [
        [
            InlineKeyboardButton(text="➕ Створити як НОВИЙ варіант", callback_data=f"save_as_new:{temp_key}")
        ],
        [
            InlineKeyboardButton(text="✏️ Оновити ОСНОВНИЙ (дефолтний)", callback_data=f"save_as_default:{temp_key}")
        ],
        [
            InlineKeyboardButton(text="❌ Скасувати", callback_data="btn_cancel_action")
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


# Short in-memory token registry for compound callback actions (keeps callback_data < 64 bytes)
_cb_registry: Dict[str, Dict[str, Any]] = {}


def register_cb_action(action_type: str, payload: Dict[str, Any]) -> str:
    """Generate a short 8-char token for callback actions."""
    import uuid
    token = uuid.uuid4().hex[:8]
    _cb_registry[token] = {"type": action_type, **payload}
    # Keep store bounded
    if len(_cb_registry) > 500:
        oldest = list(_cb_registry.keys())[:100]
        for k in oldest:
            _cb_registry.pop(k, None)
    return token


def get_cb_action(token: str) -> Optional[Dict[str, Any]]:
    """Retrieve payload for a callback token."""
    return _cb_registry.get(token)


def get_chats_list_keyboard(chats: List[Dict[str, Any]], posts_map: Dict[str, str], page: int = 0, page_size: int = 10) -> InlineKeyboardMarkup:
    """Generate paginated keyboard for connected chats."""
    total_chats = len(chats)
    total_pages = max(1, (total_chats + page_size - 1) // page_size)
    page = max(0, min(page, total_pages - 1))
    
    start_idx = page * page_size
    end_idx = start_idx + page_size
    current_chats = chats[start_idx:end_idx]
    
    keyboard = []
    for c in current_chats:
        state_icon = "🟢" if c.get("is_active") else "⏸️"
        post_title = posts_map.get(c.get("post_id"), "⭐️ Дефолт")
        btn_label = f"{state_icon} {c['chat_peer']} ➔ [{post_title}]"
        if len(btn_label) > 42:
            btn_label = btn_label[:39] + "..."
        keyboard.append([
            InlineKeyboardButton(text=btn_label, callback_data=f"chat_manage:{c['id']}:{page}")
        ])
        
    # Pagination navigation controls
    if total_pages > 1:
        nav_row = []
        if page > 0:
            nav_row.append(InlineKeyboardButton(text="⬅️ Попередня", callback_data=f"chats_page:{page - 1}"))
        else:
            nav_row.append(InlineKeyboardButton(text="⏹️", callback_data="noop"))
            
        nav_row.append(InlineKeyboardButton(text=f"📄 {page + 1}/{total_pages}", callback_data="noop"))
        
        if page < total_pages - 1:
            nav_row.append(InlineKeyboardButton(text="Наступна ➡️", callback_data=f"chats_page:{page + 1}"))
        else:
            nav_row.append(InlineKeyboardButton(text="⏹️", callback_data="noop"))
            
        keyboard.append(nav_row)
        
    keyboard.append([
        InlineKeyboardButton(text="⬅️ Головне меню", callback_data="btn_refresh")
    ])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_chat_post_select_keyboard(chat_id: str, posts: List[Dict[str, Any]], current_post_id: Optional[str], return_page: int = 0) -> InlineKeyboardMarkup:
    """Keyboard to select which post variant is assigned to a specific chat."""
    keyboard = []
    
    # Option: Default post (post_id = None)
    is_curr_default = not current_post_id
    def_check = "✅ " if is_curr_default else ""
    tok_default = register_cb_action("set_chat_post", {"chat_id": chat_id, "post_id": None, "page": return_page})
    keyboard.append([
        InlineKeyboardButton(
            text=f"{def_check}⭐️ За замовчуванням (Основне)",
            callback_data=f"act:{tok_default}"
        )
    ])
    
    for i, p in enumerate(posts, 1):
        pid = p["id"]
        title = p.get("title", f"Варіант #{i}")
        is_selected = (pid == current_post_id)
        check = "✅ " if is_selected else ""
        btn_text = f"{check}{i}. {title}"
        if len(btn_text) > 38:
            btn_text = btn_text[:35] + "..."
        tok = register_cb_action("set_chat_post", {"chat_id": chat_id, "post_id": pid, "page": return_page})
        keyboard.append([
            InlineKeyboardButton(
                text=btn_text,
                callback_data=f"act:{tok}"
            )
        ])
        
    keyboard.append([
        InlineKeyboardButton(text="⬅️ Назад до чатів", callback_data=f"chats_page:{return_page}")
    ])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_post_assign_menu_keyboard(post_id: str, chats: List[Dict[str, Any]], page: int = 0, page_size: int = 10) -> InlineKeyboardMarkup:
    """Keyboard allowing user to assign a specific post to chats (bulk or individual) with pagination."""
    total_chats = len(chats)
    total_pages = max(1, (total_chats + page_size - 1) // page_size)
    page = max(0, min(page, total_pages - 1))

    start_idx = page * page_size
    end_idx = start_idx + page_size
    current_chats = chats[start_idx:end_idx]

    keyboard = [
        [
            InlineKeyboardButton(text="⚡ Призначити на ВСІ чати", callback_data=f"post_assign_all:{post_id}"),
            InlineKeyboardButton(text="🔄 Скинути з усіх", callback_data=f"post_unassign_all:{post_id}")
        ]
    ]

    for c in current_chats:
        cid = c["id"]
        peer = c.get("chat_peer", "Чат")
        is_assigned = (c.get("post_id") == post_id)
        icon = "✅ " if is_assigned else "➕ "
        action = "unassign" if is_assigned else "assign"
        btn_text = f"{icon}{peer}"
        if len(btn_text) > 35:
            btn_text = btn_text[:32] + "..."
        tok = register_cb_action("post_toggle_chat", {"post_id": post_id, "chat_id": cid, "action": action, "page": page})
        keyboard.append([
            InlineKeyboardButton(
                text=btn_text,
                callback_data=f"act:{tok}"
            )
        ])

    # Pagination navigation
    if total_pages > 1:
        nav_row = []
        if page > 0:
            nav_row.append(InlineKeyboardButton(text="⬅️ Попередня", callback_data=f"assign_page:{post_id}:{page - 1}"))
        else:
            nav_row.append(InlineKeyboardButton(text="⏹️", callback_data="noop"))

        nav_row.append(InlineKeyboardButton(text=f"📄 {page + 1}/{total_pages}", callback_data="noop"))

        if page < total_pages - 1:
            nav_row.append(InlineKeyboardButton(text="Наступна ➡️", callback_data=f"assign_page:{post_id}:{page + 1}"))
        else:
            nav_row.append(InlineKeyboardButton(text="⏹️", callback_data="noop"))

        keyboard.append(nav_row)

    keyboard.append([
        InlineKeyboardButton(text="⬅️ Назад до поста", callback_data=f"post_view:{post_id}"),
        InlineKeyboardButton(text="📝 До списку постів", callback_data="btn_posts_list")
    ])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_back_keyboard() -> InlineKeyboardMarkup:
    """Keyboard to return to main menu."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад у головне меню", callback_data="btn_refresh")]
    ])


def get_back_to_posts_keyboard() -> InlineKeyboardMarkup:
    """Keyboard to return to posts list."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ До списку постів", callback_data="btn_posts_list")]
    ])

