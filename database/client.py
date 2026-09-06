import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Optional, Dict, Any
from supabase import create_client, Client
from config import config

logger = logging.getLogger("Database")

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
CHAT_POSTS_FILE = DATA_DIR / "chat_posts.json"


class SupabaseDB:
    def __init__(self):
        self.client: Optional[Client] = None
        self._init_client()

    def _init_client(self):
        url = (config.SUPABASE_URL or os.getenv("SUPABASE_URL", "")).strip().strip('"').strip("'")
        key = (config.SUPABASE_KEY or os.getenv("SUPABASE_KEY", "")).strip().strip('"').strip("'")
        if not url or not key:
            logger.error(f"Supabase URL or Key not set in configuration! (URL={'set' if url else 'empty'}, KEY={'set' if key else 'empty'})")
            return
        try:
            self.client = create_client(url, key)
            logger.info("Supabase client successfully initialized.")
        except Exception as e:
            logger.error(f"Failed to initialize Supabase client: {e}")

    def _ensure_client(self) -> bool:
        if not self.client:
            self._init_client()
        return bool(self.client)

    # ==================== LOCAL PERSISTENCE HELPERS ====================

    def _load_local_chat_posts(self) -> Dict[str, str]:
        if not CHAT_POSTS_FILE.exists():
            return {}
        try:
            with open(CHAT_POSTS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_local_chat_posts(self, data: Dict[str, str]):
        try:
            with open(CHAT_POSTS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Error saving local chat posts mapping: {e}")

    def _merge_chat_post_ids(self, chats: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        local_map = self._load_local_chat_posts()
        for c in chats:
            if not c.get("post_id") and c["id"] in local_map:
                c["post_id"] = local_map[c["id"]]
        return chats

    # ==================== CHATS CRUD ====================

    def get_all_chats(self) -> List[Dict[str, Any]]:
        """Retrieve all registered target chats with post assignments."""
        if not self._ensure_client():
            return []
        try:
            res = self.client.table("chats").select("*").order("created_at", desc=False).execute()
            chats = res.data or []
            return self._merge_chat_post_ids(chats)
        except Exception as e:
            logger.error(f"Error fetching chats: {e}")
            return []

    def get_due_chats(self) -> List[Dict[str, Any]]:
        """Retrieve active chats that are ready for posting (next_post_at <= now)."""
        if not self.client:
            return []
        try:
            now_iso = datetime.now(timezone.utc).isoformat()
            res = (
                self.client.table("chats")
                .select("*")
                .eq("is_active", True)
                .lte("next_post_at", now_iso)
                .order("next_post_at", desc=False)
                .execute()
            )
            chats = res.data or []
            return self._merge_chat_post_ids(chats)
        except Exception as e:
            logger.error(f"Error fetching due chats: {e}")
            return []

    def get_chat_by_id(self, chat_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a single chat by its ID."""
        if not self.client:
            return None
        try:
            res = self.client.table("chats").select("*").eq("id", chat_id).execute()
            if res.data:
                chats = self._merge_chat_post_ids(res.data)
                return chats[0]
            return None
        except Exception as e:
            logger.error(f"Error fetching chat {chat_id}: {e}")
            return None

    def add_chat(
        self,
        chat_peer: str,
        interval_minutes: int = 60,
        title: str = "",
        post_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Add or update a target chat with optional specific post_id."""
        if not self.client:
            return None
        chat_peer = chat_peer.strip()
        if not chat_peer:
            return None
        try:
            now_iso = datetime.now(timezone.utc).isoformat()
            data = {
                "chat_peer": chat_peer,
                "interval_minutes": interval_minutes,
                "title": title or chat_peer,
                "is_active": True,
                "status": "active",
                "next_post_at": now_iso,
                "updated_at": now_iso
            }
            if post_id:
                data["post_id"] = post_id
            
            chat_record = None
            try:
                res = self.client.table("chats").upsert(data, on_conflict="chat_peer").execute()
                chat_record = res.data[0] if res.data else None
            except Exception as upsert_err:
                if "post_id" in str(upsert_err) or "PGRST204" in str(upsert_err):
                    data.pop("post_id", None)
                    res = self.client.table("chats").upsert(data, on_conflict="chat_peer").execute()
                    chat_record = res.data[0] if res.data else None
                else:
                    raise upsert_err

            if chat_record and post_id:
                local_map = self._load_local_chat_posts()
                local_map[chat_record["id"]] = post_id
                self._save_local_chat_posts(local_map)
                chat_record["post_id"] = post_id

            return chat_record
        except Exception as e:
            logger.error(f"Error adding chat {chat_peer}: {e}")
            return None

    def update_chat(self, chat_id: str, updates: Dict[str, Any]) -> bool:
        """Update fields of a specific chat."""
        if not self.client:
            return False
        try:
            # Handle post_id in local storage
            if "post_id" in updates:
                local_map = self._load_local_chat_posts()
                if updates["post_id"]:
                    local_map[chat_id] = updates["post_id"]
                else:
                    local_map.pop(chat_id, None)
                self._save_local_chat_posts(local_map)

            updates["updated_at"] = datetime.now(timezone.utc).isoformat()
            try:
                res = self.client.table("chats").update(updates).eq("id", chat_id).execute()
                return bool(res.data)
            except Exception as upd_err:
                if "post_id" in str(upd_err) or "PGRST204" in str(upd_err):
                    cleaned = {k: v for k, v in updates.items() if k != "post_id"}
                    if cleaned:
                        self.client.table("chats").update(cleaned).eq("id", chat_id).execute()
                    # Return True because post_id was successfully saved locally!
                    return True
                raise upd_err
        except Exception as e:
            logger.error(f"Error updating chat {chat_id}: {e}")
            return False

    def delete_chat(self, chat_id: str) -> bool:
        """Delete a chat from monitoring."""
        if not self.client:
            return False
        try:
            local_map = self._load_local_chat_posts()
            local_map.pop(chat_id, None)
            self._save_local_chat_posts(local_map)

            res = self.client.table("chats").delete().eq("id", chat_id).execute()
            return bool(res.data)
        except Exception as e:
            logger.error(f"Error deleting chat {chat_id}: {e}")
            return False

    def update_chat_post_success(self, chat_id: str, interval_minutes: int, jitter_sec: int = 0):
        """Update chat after a successful post with next calculated time."""
        now = datetime.now(timezone.utc)
        next_post = now + timedelta(minutes=interval_minutes, seconds=jitter_sec)
        self.update_chat(chat_id, {
            "last_posted_at": now.isoformat(),
            "next_post_at": next_post.isoformat(),
            "status": "active",
            "last_error": None
        })

    def update_chat_delay(self, chat_id: str, delay_seconds: int, status_msg: str = "slowmode_wait", error_msg: Optional[str] = None):
        """Delay the next posting time due to slowmode, floodwait, or error."""
        now = datetime.now(timezone.utc)
        next_post = now + timedelta(seconds=delay_seconds)
        self.update_chat(chat_id, {
            "next_post_at": next_post.isoformat(),
            "status": status_msg,
            "last_error": error_msg
        })

    # ==================== POSTS / TEMPLATES ====================

    def get_active_post(self) -> Optional[Dict[str, Any]]:
        """Get the default fallback ad template (newest active post)."""
        if not self.client:
            return None
        try:
            res = (
                self.client.table("posts")
                .select("*")
                .eq("is_active", True)
                .order("updated_at", desc=True)
                .limit(1)
                .execute()
            )
            if res.data:
                return res.data[0]
            # Fallback to the latest post if none marked active
            res = (
                self.client.table("posts")
                .select("*")
                .order("updated_at", desc=True)
                .limit(1)
                .execute()
            )
            return res.data[0] if res.data else None
        except Exception as e:
            logger.error(f"Error fetching active post: {e}")
            return None

    def get_post_by_id(self, post_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific post template by ID."""
        if not self.client or not post_id:
            return None
        try:
            res = self.client.table("posts").select("*").eq("id", post_id).limit(1).execute()
            return res.data[0] if res.data else None
        except Exception as e:
            logger.error(f"Error fetching post {post_id}: {e}")
            return None

    def get_all_posts(self) -> List[Dict[str, Any]]:
        """Get all post templates with assigned chat counts."""
        if not self.client:
            return []
        try:
            res = self.client.table("posts").select("*").order("created_at", desc=False).execute()
            posts = res.data or []
            
            # Add chat statistics
            chats = self.get_all_chats()
            chat_count_map = {}
            default_count = 0
            for c in chats:
                pid = c.get("post_id")
                if pid:
                    chat_count_map[pid] = chat_count_map.get(pid, 0) + 1
                else:
                    default_count += 1

            for p in posts:
                p["assigned_chats_count"] = chat_count_map.get(p["id"], 0)
                if p.get("is_active"):
                    p["default_chats_count"] = default_count

            return posts
        except Exception as e:
            logger.error(f"Error fetching all posts: {e}")
            return []

    def create_post(
        self,
        content: str,
        title: str = "Оголошення",
        source_msg_id: Optional[int] = None,
        source_chat_peer: str = "me",
        is_active: bool = False
    ) -> Optional[Dict[str, Any]]:
        """Create a new post template."""
        if not self.client:
            return None
        try:
            now_iso = datetime.now(timezone.utc).isoformat()
            if is_active:
                try:
                    self.client.table("posts").update({"is_active": False}).neq("title", "__non_existent__").execute()
                except Exception as err:
                    logger.warning(f"Could not deactivate other posts: {err}")

            data = {
                "title": title,
                "content": content,
                "is_active": is_active,
                "source_msg_id": source_msg_id,
                "source_chat_peer": source_chat_peer,
                "created_at": now_iso,
                "updated_at": now_iso
            }
            res = self.client.table("posts").insert(data).execute()
            return res.data[0] if res.data else None
        except Exception as e:
            logger.error(f"Error creating post: {e}")
            return None

    def update_post(self, post_id: str, updates: Dict[str, Any]) -> bool:
        """Update a post template."""
        if not self.client or not post_id:
            return False
        try:
            updates["updated_at"] = datetime.now(timezone.utc).isoformat()
            if updates.get("is_active"):
                self.client.table("posts").update({"is_active": False}).neq("id", post_id).execute()
            res = self.client.table("posts").update(updates).eq("id", post_id).execute()
            return bool(res.data)
        except Exception as e:
            logger.error(f"Error updating post {post_id}: {e}")
            return False

    def delete_post(self, post_id: str) -> bool:
        """Delete a post template and unlink it from any chats."""
        if not self.client or not post_id:
            return False
        try:
            local_map = self._load_local_chat_posts()
            local_map = {cid: pid for cid, pid in local_map.items() if pid != post_id}
            self._save_local_chat_posts(local_map)

            # Unlink from chats before deletion in Supabase if column exists
            try:
                self.client.table("chats").update({"post_id": None}).eq("post_id", post_id).execute()
            except Exception as unl_err:
                logger.warning(f"Could not unlink chats in DB from post {post_id}: {unl_err}")

            res = self.client.table("posts").delete().eq("id", post_id).execute()
            return bool(res.data)
        except Exception as e:
            logger.error(f"Error deleting post {post_id}: {e}")
            return False

    def save_post(
        self,
        content: str,
        title: str = "Оголошення",
        post_id: Optional[str] = None,
        source_msg_id: Optional[int] = None,
        source_chat_peer: str = "me",
        make_active: bool = False
    ) -> Optional[Dict[str, Any]]:
        """Create or update a post template."""
        if not self.client:
            return None
        try:
            if post_id:
                updates = {
                    "content": content,
                    "title": title
                }
                if source_msg_id is not None:
                    updates["source_msg_id"] = source_msg_id
                    updates["source_chat_peer"] = source_chat_peer
                if make_active:
                    updates["is_active"] = True
                self.update_post(post_id, updates)
                return self.get_post_by_id(post_id)
            else:
                return self.create_post(
                    content=content,
                    title=title,
                    source_msg_id=source_msg_id,
                    source_chat_peer=source_chat_peer,
                    is_active=make_active
                )
        except Exception as e:
            logger.error(f"Error saving post: {e}")
            return None

    def set_default_post(self, post_id: str) -> bool:
        """Mark a post template as the default active fallback."""
        if not self.client or not post_id:
            return False
        try:
            self.client.table("posts").update({"is_active": False}).neq("id", post_id).execute()
            res = self.client.table("posts").update({
                "is_active": True,
                "updated_at": datetime.now(timezone.utc).isoformat()
            }).eq("id", post_id).execute()
            return bool(res.data)
        except Exception as e:
            logger.error(f"Error setting default post {post_id}: {e}")
            return False

    def set_active_post(self, post_id: str) -> bool:
        """Alias for set_default_post."""
        return self.set_default_post(post_id)

    def assign_post_to_chats(self, post_id: Optional[str], chat_ids: List[str]) -> bool:
        """Assign a specific post (or None for default) to chats."""
        if not self.client or not chat_ids:
            return False
        try:
            for cid in chat_ids:
                self.update_chat(cid, {"post_id": post_id})
            return True
        except Exception as e:
            logger.error(f"Error assigning post to chats: {e}")
            return False

    # ==================== SETTINGS ====================

    def get_settings(self) -> Dict[str, Any]:
        """Fetch global settings with all security parameters."""
        default = {
            "id": 1,
            "is_running": False,
            "min_delay_seconds": 15,
            "max_delay_seconds": 35,
            "jitter_minutes": 3,
            "max_posts_per_hour": 30,
            "max_posts_per_day": 300,
            "batch_size": 8,
            "batch_rest_minutes": 8,
            "enable_typing_simulation": True,
            "typing_duration_seconds": 4,
            "enable_spintax": True,
            "enable_anti_fingerprint": True,
            "enable_night_mode": False,
            "night_start_hour": 23,
            "night_end_hour": 8,
            "auto_circuit_breaker": True
        }
        if not self.client:
            return default
        try:
            res = self.client.table("settings").select("*").eq("id", 1).execute()
            if res.data:
                # Merge with default in case new columns were added
                merged = {**default, **res.data[0]}
                return merged
            self.client.table("settings").upsert(default).execute()
            return default
        except Exception as e:
            logger.error(f"Error fetching settings: {e}")
            return default

    def update_settings(self, updates: Dict[str, Any]) -> bool:
        """Update global settings."""
        if not self.client:
            return False
        try:
            updates["updated_at"] = datetime.now(timezone.utc).isoformat()
            res = self.client.table("settings").update(updates).eq("id", 1).execute()
            return bool(res.data)
        except Exception as e:
            logger.error(f"Error updating settings: {e}")
            return False

    def toggle_poster(self, is_running: bool) -> bool:
        """Enable or disable global posting worker."""
        return self.update_settings({"is_running": is_running})

    # ==================== LOGS & STATS ====================

    def add_log(self, chat_peer: str, status: str, details: str = ""):
        """Write an entry to the log table."""
        if not self.client:
            return
        try:
            self.client.table("logs").insert({
                "chat_peer": chat_peer,
                "status": status,
                "details": details
            }).execute()
        except Exception as e:
            logger.error(f"Error adding log: {e}")

    def get_recent_logs(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Retrieve recent logs for display."""
        if not self.client:
            return []
        try:
            res = self.client.table("logs").select("*").order("created_at", desc=True).limit(limit).execute()
            return res.data or []
        except Exception as e:
            logger.error(f"Error fetching logs: {e}")
            return []

    def get_hourly_post_count(self) -> int:
        """Count successful posts in the last 60 minutes."""
        if not self.client:
            return 0
        try:
            one_hour_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
            res = self.client.table("logs").select("id", count="exact").eq("status", "success").gte("created_at", one_hour_ago).execute()
            return res.count or 0
        except Exception:
            return 0

    def get_daily_post_count(self) -> int:
        """Count successful posts today (UTC)."""
        if not self.client:
            return 0
        try:
            today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
            res = self.client.table("logs").select("id", count="exact").eq("status", "success").gte("created_at", today_start).execute()
            return res.count or 0
        except Exception:
            return 0

    def get_existing_peers_set(self) -> set[str]:
        """Return a set of normalized lowercase chat_peers already in the chats table."""
        if not self.client:
            return set()
        try:
            res = self.client.table("chats").select("chat_peer").execute()
            existing = set()
            for row in (res.data or []):
                peer = row.get("chat_peer", "").strip().lower()
                if peer:
                    existing.add(peer)
                    if peer.startswith("@"):
                        existing.add(peer[1:])
                    else:
                        existing.add(f"@{peer}")
            return existing
        except Exception as e:
            logger.error(f"Error fetching existing peers set: {e}")
            return set()

    # ==================== CONTEXTUAL DISCOVERY ====================

    def create_discovery_search(self, query: str, intent: str = "advertising_source") -> Optional[Dict[str, Any]]:
        """Create a new discovery search session."""
        if not self.client:
            return None
        try:
            res = self.client.table("discovery_searches").insert({
                "query": query,
                "intent": intent,
                "status": "searching",
                "total_candidates": 0,
                "total_relevant": 0
            }).execute()
            return res.data[0] if res.data else None
        except Exception as e:
            logger.error(f"Error creating discovery search: {e}")
            return None

    def update_discovery_search(self, search_id: str, updates: Dict[str, Any]) -> bool:
        """Update discovery search session status and counts."""
        if not self.client:
            return False
        try:
            self.client.table("discovery_searches").update(updates).eq("id", search_id).execute()
            return True
        except Exception as e:
            logger.error(f"Error updating discovery search: {e}")
            return False

    def get_discovery_search(self, search_id: str) -> Optional[Dict[str, Any]]:
        """Fetch discovery search session by ID."""
        if not self.client:
            return None
        try:
            res = self.client.table("discovery_searches").select("*").eq("id", search_id).execute()
            return res.data[0] if res.data else None
        except Exception as e:
            logger.error(f"Error fetching discovery search: {e}")
            return None

    def save_discovery_queries(self, search_id: str, queries: List[Dict[str, Any]]):
        """Save generated sub-queries."""
        if not self.client or not queries:
            return
        try:
            rows = [
                {
                    "search_id": search_id,
                    "query_text": q.get("text") or q.get("query_text", ""),
                    "query_type": q.get("type") or q.get("query_type", "related"),
                    "priority": q.get("priority", 1),
                    "results_count": q.get("results_count", 0)
                }
                for q in queries
            ]
            self.client.table("discovery_queries").insert(rows).execute()
        except Exception as e:
            logger.error(f"Error saving discovery queries: {e}")

    def save_discovery_results(self, search_id: str, candidates: List[Dict[str, Any]]) -> int:
        """Save ranked candidate results."""
        if not self.client or not candidates:
            return 0
        try:
            rows = []
            for c in candidates:
                rows.append({
                    "search_id": search_id,
                    "telegram_peer": c.get("peer") or c.get("telegram_peer", ""),
                    "title": c.get("title", ""),
                    "description": (c.get("description") or "")[:500],
                    "chat_type": c.get("type") or c.get("chat_type", "group"),
                    "members_count": c.get("participants_count") or c.get("members_count", 0),
                    "can_post": c.get("can_post", True),
                    "matched_query": c.get("matched_query", ""),
                    "relevance_score": int(c.get("relevance_score", 0)),
                    "promo_score": int(c.get("promo_score", 0)),
                    "activity_score": int(c.get("activity_score", 0)),
                    "final_score": int(c.get("final_score", 0)),
                    "ai_reason": c.get("ai_reason", ""),
                    "classification": c.get("classification", "relevant"),
                    "status": c.get("status", "new")
                })
            res = self.client.table("discovery_results").insert(rows).execute()
            return len(res.data or [])
        except Exception as e:
            logger.error(f"Error saving discovery results: {e}")
            return 0

    def get_discovery_results(
        self,
        search_id: str,
        min_score: int = 0,
        chat_type: str = "all",
        status: str = "all",
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Retrieve ranked results for a search session with filters."""
        if not self.client:
            return []
        try:
            q = self.client.table("discovery_results").select("*").eq("search_id", search_id)
            if min_score > 0:
                q = q.gte("final_score", min_score)
            if chat_type == "group":
                q = q.in_("chat_type", ["group", "supergroup"])
            elif chat_type == "channel":
                q = q.eq("chat_type", "channel")
            if status != "all":
                q = q.eq("status", status)
            else:
                q = q.neq("status", "hidden")

            res = q.order("final_score", desc=True).limit(limit).execute()
            return res.data or []
        except Exception as e:
            logger.error(f"Error fetching discovery results: {e}")
            return []

    def update_discovery_result_status(self, result_id: str, status: str) -> bool:
        """Mark result as 'added_to_posting', 'hidden', etc."""
        if not self.client:
            return False
        try:
            self.client.table("discovery_results").update({"status": status}).eq("id", result_id).execute()
            return True
        except Exception as e:
            logger.error(f"Error updating discovery result status: {e}")
            return False


# Global database instance
db = SupabaseDB()
