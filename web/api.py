import asyncio
import re
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import List, Optional
from pathlib import Path
from database.client import db
from core.client import client, get_latest_saved_message
from core.spintax import SpintaxEngine
from core.finder import ChatFinder
from core.poster import poster_worker
from core.ai_discovery import AIDiscoveryEngine

web_app = FastAPI(title="Telegram Auto-Poster Dashboard")

STATIC_DIR = Path(__file__).resolve().parent / "static"
web_app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# Pydantic Schemas
class ChatCreateRequest(BaseModel):
    chat_peer: str
    interval_minutes: int = 60
    title: Optional[str] = ""
    post_id: Optional[str] = None

class BatchChatsRequest(BaseModel):
    text_links: str
    interval_minutes: int = 60
    post_id: Optional[str] = None

class ChatUpdateRequest(BaseModel):
    interval_minutes: Optional[int] = None
    is_active: Optional[bool] = None
    title: Optional[str] = None
    reset_status: Optional[bool] = False
    post_id: Optional[str] = None

class PostSaveRequest(BaseModel):
    content: str
    title: Optional[str] = "Головне оголошення"
    post_id: Optional[str] = None
    source_msg_id: Optional[int] = None
    source_chat_peer: Optional[str] = "me"
    is_active: Optional[bool] = False

class SyncSavedPostRequest(BaseModel):
    post_id: Optional[str] = None
    create_new: Optional[bool] = False

class SpintaxPreviewRequest(BaseModel):
    text: str

class SearchChatsRequest(BaseModel):
    query: str
    limit: Optional[int] = 40

class JoinAddChatRequest(BaseModel):
    chat_peer: str
    interval_minutes: int = 60
    title: Optional[str] = ""
    post_id: Optional[str] = None

class BatchJoinAddRequest(BaseModel):
    items: List[JoinAddChatRequest]

class SettingsUpdateRequest(BaseModel):
    is_running: Optional[bool] = None
    min_delay_seconds: Optional[int] = None
    max_delay_seconds: Optional[int] = None
    jitter_minutes: Optional[int] = None
    max_posts_per_hour: Optional[int] = None
    max_posts_per_day: Optional[int] = None
    batch_size: Optional[int] = None
    batch_rest_minutes: Optional[int] = None
    enable_typing_simulation: Optional[bool] = None
    typing_duration_seconds: Optional[int] = None
    enable_spintax: Optional[bool] = None
    enable_anti_fingerprint: Optional[bool] = None
    enable_night_mode: Optional[bool] = None
    night_start_hour: Optional[int] = None
    night_end_hour: Optional[int] = None
    auto_circuit_breaker: Optional[bool] = None


@web_app.get("/")
async def serve_index():
    return FileResponse(STATIC_DIR / "index.html")


@web_app.get("/api/status")
async def get_system_status():
    is_authorized = False
    user_info = None
    try:
        if client.is_connected() and await client.is_user_authorized():
            is_authorized = True
            me = await client.get_me()
            user_info = {
                "name": f"{me.first_name} {me.last_name or ''}".strip(),
                "username": me.username,
                "phone": me.phone,
                "is_premium": getattr(me, "premium", False)
            }
    except Exception:
        pass

    settings = db.get_settings()
    chats = db.get_all_chats()
    active_chats = sum(1 for c in chats if c.get("is_active"))
    error_chats = sum(1 for c in chats if c.get("status") in ["error", "restricted"])
    hourly_count = db.get_hourly_post_count()
    daily_count = db.get_daily_post_count()

    return {
        "is_authorized": is_authorized,
        "user": user_info,
        "is_running": settings.get("is_running", False),
        "total_chats": len(chats),
        "active_chats": active_chats,
        "error_chats": error_chats,
        "hourly_posts": hourly_count,
        "daily_posts": daily_count,
        "settings": settings
    }


@web_app.post("/api/settings/toggle")
async def toggle_poster():
    settings = db.get_settings()
    new_state = not settings.get("is_running", False)
    db.toggle_poster(new_state)
    return {"is_running": new_state}


@web_app.post("/api/settings")
async def update_settings(req: SettingsUpdateRequest):
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    db.update_settings(updates)
    return {"status": "ok", "settings": db.get_settings()}


@web_app.get("/api/chats")
async def get_chats():
    return db.get_all_chats()


@web_app.post("/api/chats")
async def add_chat(req: ChatCreateRequest):
    post_id = req.post_id if req.post_id and req.post_id != "default" else None
    res = db.add_chat(req.chat_peer, req.interval_minutes, req.title or "", post_id=post_id)
    if not res:
        raise HTTPException(status_code=400, detail="Не вдалося додати чат")
    return res


@web_app.post("/api/chats/batch")
async def add_chats_batch(req: BatchChatsRequest):
    """Parse text and extract multiple chat usernames/links."""
    lines = req.text_links.strip().split("\n")
    added = 0
    post_id = req.post_id if req.post_id and req.post_id != "default" else None
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        match = re.search(r"(?:https?://t\.me/)?(@?[a-zA-Z0-9_+\-]+)", line)
        if match:
            peer = match.group(1)
            if not peer.startswith("+") and not peer.startswith("@") and not peer.startswith("joinchat"):
                peer = f"@{peer}"
            db.add_chat(peer, req.interval_minutes, post_id=post_id)
            added += 1
    return {"status": "ok", "added_count": added}


@web_app.put("/api/chats/{chat_id}")
async def update_chat(chat_id: str, req: ChatUpdateRequest):
    updates = {k: v for k, v in req.model_dump().items() if v is not None and k != "reset_status"}
    if req.reset_status:
        updates["status"] = "active"
        updates["last_error"] = None
        updates["is_active"] = True
    if "post_id" in updates and (updates["post_id"] == "default" or updates["post_id"] == ""):
        updates["post_id"] = None
    db.update_chat(chat_id, updates)
    return {"status": "ok"}


@web_app.delete("/api/chats/{chat_id}")
async def delete_chat(chat_id: str):
    success = db.delete_chat(chat_id)
    return {"status": "ok" if success else "error"}


@web_app.get("/api/posts")
async def get_posts():
    return {
        "active_post": db.get_active_post(),
        "all_posts": db.get_all_posts()
    }


@web_app.get("/api/posts/{post_id}")
async def get_single_post(post_id: str):
    post = db.get_post_by_id(post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Пост не знайдено")
    return post


@web_app.post("/api/posts")
async def save_post(req: PostSaveRequest):
    if req.post_id:
        updates = {
            "content": req.content,
            "title": req.title or "Оголошення"
        }
        if req.source_msg_id is not None:
            updates["source_msg_id"] = req.source_msg_id
            updates["source_chat_peer"] = req.source_chat_peer or "me"
        if req.is_active:
            updates["is_active"] = True
        db.update_post(req.post_id, updates)
        res = db.get_post_by_id(req.post_id)
    else:
        res = db.create_post(
            content=req.content,
            title=req.title or "Оголошення",
            source_msg_id=req.source_msg_id,
            source_chat_peer=req.source_chat_peer or "me",
            is_active=bool(req.is_active)
        )
    return {"status": "ok", "post": res}


@web_app.delete("/api/posts/{post_id}")
async def delete_post(post_id: str):
    all_posts = db.get_all_posts()
    if len(all_posts) <= 1:
        raise HTTPException(status_code=400, detail="Неможливо видалити останній варіант оголошення.")
    success = db.delete_post(post_id)
    return {"status": "ok" if success else "error"}


class GenerateTitleRequest(BaseModel):
    content: str


@web_app.post("/api/posts/generate-title")
async def generate_post_title(req: GenerateTitleRequest):
    title = AIDiscoveryEngine.generate_title_for_post(req.content)
    return {"status": "ok", "title": title}


@web_app.post("/api/posts/{post_id}/set-default")
async def set_default_post(post_id: str):
    success = db.set_default_post(post_id)
    return {"status": "ok" if success else "error"}


@web_app.post("/api/posts/preview-spintax")
async def preview_spintax(req: SpintaxPreviewRequest):
    sample = SpintaxEngine.parse(req.text)
    return {"sample": sample}


@web_app.get("/api/telegram/saved-post")
async def fetch_from_saved_messages():
    """Import the latest message from Saved Messages with Telegram Premium Custom Emojis and message_id."""
    res = await get_latest_saved_message()
    return res


@web_app.post("/api/telegram/sync-saved-post")
async def sync_saved_post(req: Optional[SyncSavedPostRequest] = None):
    """Fetch latest message from 'Saved Messages' (me) and save it."""
    res = await get_latest_saved_message()
    if res.get("status") != "ok":
        return res

    msg_id = res.get("message_id")
    html_text = res.get("html_text") or res.get("raw_text") or ""
    
    post_id = req.post_id if req else None
    create_new = req.create_new if req else False

    if post_id and not create_new:
        db.update_post(post_id, {
            "content": html_text,
            "source_msg_id": msg_id,
            "source_chat_peer": "me"
        })
        saved_post = db.get_post_by_id(post_id)
    else:
        saved_post = db.create_post(
            content=html_text,
            title=f"Зі Збережених #{msg_id}",
            source_msg_id=msg_id,
            source_chat_peer="me",
            is_active=False
        )

    return {
        "status": "ok",
        "message": f"Оголошення #{msg_id} успішно завантажено!",
        "post": saved_post,
        "message_id": msg_id,
        "has_custom_emojis": res.get("has_custom_emojis"),
        "entities_count": res.get("entities_count")
    }


class DiscoverySearchRequest(BaseModel):
    query: str
    limit: Optional[int] = 40

class DiscoveryAddToPostingRequest(BaseModel):
    result_id: Optional[str] = None
    chat_peer: str
    interval_minutes: int = 60
    title: Optional[str] = ""
    post_id: Optional[str] = None

class DiscoveryBatchAddRequest(BaseModel):
    items: List[DiscoveryAddToPostingRequest]

class DiscoveryHideRequest(BaseModel):
    result_id: str


# ==================== CONTEXTUAL AI DISCOVERY ====================

@web_app.post("/api/discovery/search")
async def start_discovery_search(req: DiscoverySearchRequest):
    """Start an asynchronous AI-powered contextual discovery task."""
    query = req.query.strip()
    if not query:
        raise HTTPException(status_code=400, detail="Введіть пошуковий запит")

    search_session = db.create_discovery_search(query)
    if not search_session:
        raise HTTPException(status_code=500, detail="Не вдалося створити сесію пошуку")

    search_id = search_session["id"]
    # Run in background
    asyncio.create_task(ChatFinder.run_contextual_discovery(query, search_id, req.limit or 40))

    return {
        "status": "searching",
        "search_id": search_id,
        "query": query
    }


@web_app.get("/api/discovery/results/{search_id}")
async def get_discovery_search_results(
    search_id: str,
    min_score: int = 0,
    chat_type: str = "all",
    status: str = "all"
):
    """Fetch progressive results for an active or completed discovery session."""
    search_info = db.get_discovery_search(search_id)
    if not search_info:
        raise HTTPException(status_code=404, detail="Сесію пошуку не знайдено")

    results = db.get_discovery_results(
        search_id=search_id,
        min_score=min_score,
        chat_type=chat_type,
        status=status
    )

    return {
        "search": search_info,
        "results": results,
        "is_completed": search_info.get("status") in ["completed", "failed"]
    }


@web_app.post("/api/discovery/add-to-posting")
async def discovery_add_to_posting(req: DiscoveryAddToPostingRequest):
    """Join candidate chat and add to auto-poster schedule."""
    res = await ChatFinder.join_and_add_chat(
        peer_str=req.chat_peer,
        interval_minutes=req.interval_minutes,
        title=req.title or ""
    )
    if req.result_id and res.get("status") == "ok":
        db.update_discovery_result_status(req.result_id, "added_to_posting")

    return res


@web_app.post("/api/discovery/batch-add")
async def discovery_batch_add(req: DiscoveryBatchAddRequest):
    """Batch join and add multiple candidate chats."""
    added = 0
    for item in req.items:
        res = await ChatFinder.join_and_add_chat(
            peer_str=item.chat_peer,
            interval_minutes=item.interval_minutes,
            title=item.title or ""
        )
        if res.get("status") == "ok":
            added += 1
            if item.result_id:
                db.update_discovery_result_status(item.result_id, "added_to_posting")
        await asyncio.sleep(0.5)

    return {"status": "ok", "added_count": added}


@web_app.post("/api/discovery/hide")
async def discovery_hide_result(req: DiscoveryHideRequest):
    """Hide an irrelevant discovery candidate."""
    success = db.update_discovery_result_status(req.result_id, "hidden")
    return {"status": "ok" if success else "error"}


# ==================== CHAT FINDER & PARSER ====================

@web_app.post("/api/finder/search")
async def search_chats(req: SearchChatsRequest):
    """Search Telegram public groups by keywords."""
    results = await ChatFinder.search_chats(req.query, req.limit or 40)
    return {"status": "ok", "results": results}


@web_app.post("/api/finder/add")
async def finder_add_chat(req: JoinAddChatRequest):
    """Join and add a found chat directly."""
    res = await ChatFinder.join_and_add_chat(
        peer_str=req.chat_peer,
        interval_minutes=req.interval_minutes,
        title=req.title or ""
    )
    return res


@web_app.post("/api/finder/batch-add")
async def finder_batch_add(req: BatchJoinAddRequest):
    """Batch join and add multiple chats."""
    added = 0
    for item in req.items:
        res = await ChatFinder.join_and_add_chat(
            peer_str=item.chat_peer,
            interval_minutes=item.interval_minutes,
            title=item.title or ""
        )
        if res.get("status") == "ok":
            added += 1
    return {"status": "ok", "added_count": added}


@web_app.get("/api/logs")
async def get_logs(limit: int = 50):
    return db.get_recent_logs(limit)
