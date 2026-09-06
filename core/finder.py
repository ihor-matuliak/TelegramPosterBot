import asyncio
import logging
import re
from typing import List, Dict, Any, Optional, Set
from telethon.tl.functions.contacts import SearchRequest
from telethon.tl.functions.messages import SearchGlobalRequest
from telethon.tl.functions.channels import JoinChannelRequest, GetFullChannelRequest
from telethon.tl.types import (
    Channel,
    Chat,
    InputPeerChannel,
    InputPeerChat,
    InputMessagesFilterEmpty
)
from core.client import client
from core.ai_discovery import AIDiscoveryEngine
from database.client import db

logger = logging.getLogger("ChatFinder")


class ChatFinder:
    @staticmethod
    async def join_and_add_chat(peer_str: str, interval_minutes: int = 60, title: str = "", post_id: Optional[str] = None) -> Dict[str, Any]:
        """Join a Telegram public group and add it to the poster schedule."""
        if not client.is_connected() or not await client.is_user_authorized():
            return {"status": "error", "message": "Telegram клієнт не авторизований"}

        peer_str = peer_str.strip()
        if not peer_str.startswith("@") and not peer_str.startswith("http") and not peer_str.startswith("-"):
            peer_str = f"@{peer_str}"

        try:
            entity = await client.get_entity(peer_str)
            resolved_title = getattr(entity, "title", title or peer_str)

            if isinstance(entity, Channel):
                try:
                    await client(JoinChannelRequest(entity))
                    logger.info(f"Successfully joined {peer_str}")
                except Exception as join_err:
                    logger.warning(f"Could not join {peer_str} (might be already member or restricted): {join_err}")

            res = db.add_chat(peer_str, interval_minutes=interval_minutes, title=resolved_title, post_id=post_id)
            if res:
                return {
                    "status": "ok",
                    "message": f"Чат '{resolved_title}' успішно додано до розсилки!",
                    "chat": res
                }
            else:
                return {"status": "error", "message": "Не вдалося зберегти чат у базі даних"}

        except Exception as e:
            logger.error(f"Error joining/adding chat {peer_str}: {e}")
            return {"status": "error", "message": f"Помилка: {str(e)}"}

    @classmethod
    async def search_single_query(
        cls,
        query: str,
        limit: int = 30,
        excluded_peers: Optional[Set[str]] = None
    ) -> List[Dict[str, Any]]:
        """Search Telegram public groups and channels by keyword query."""
        if not client.is_connected() or not await client.is_user_authorized():
            return []

        query = query.strip()
        if not query:
            return []

        excluded = excluded_peers or set()
        candidates = []
        seen_ids = set()

        try:
            # 1. Directory Search (contacts.Search)
            try:
                search_res = await client(SearchRequest(q=query, limit=limit))
                for chat in search_res.chats:
                    cand = cls._parse_telegram_chat(chat, query, excluded)
                    if cand and cand["id"] not in seen_ids:
                        seen_ids.add(cand["id"])
                        candidates.append(cand)
            except Exception as dir_err:
                logger.warning(f"Directory search error for '{query}': {dir_err}")

            # 2. Global Messages Search (messages.SearchGlobal)
            try:
                msg_res = await client(SearchGlobalRequest(
                    q=query,
                    filter=InputMessagesFilterEmpty(),
                    min_date=None,
                    max_date=None,
                    offset_rate=0,
                    offset_peer=InputPeerChannel(0, 0) if False else None,
                    offset_id=0,
                    limit=min(20, limit)
                ))
                if hasattr(msg_res, "chats"):
                    for chat in msg_res.chats:
                        cand = cls._parse_telegram_chat(chat, query, excluded)
                        if cand and cand["id"] not in seen_ids:
                            seen_ids.add(cand["id"])
                            candidates.append(cand)
            except Exception as msg_err:
                logger.debug(f"Global message search notice for '{query}': {msg_err}")

            return candidates
        except Exception as e:
            logger.error(f"Error in search_single_query for '{query}': {e}")
            return []

    @classmethod
    def _parse_telegram_chat(cls, chat: Any, matched_query: str, excluded_peers: Set[str]) -> Optional[Dict[str, Any]]:
        """Extract and normalize candidate chat metadata."""
        username = getattr(chat, "username", None)
        if not username:
            return None

        peer_str = f"@{username}"
        clean_peer = username.lower()
        if peer_str.lower() in excluded_peers or clean_peer in excluded_peers:
            return None

        is_group = False
        can_post = True
        chat_type = "channel"
        participants_count = getattr(chat, "participants_count", 0) or 0
        title = getattr(chat, "title", username)

        if isinstance(chat, Channel):
            if chat.megagroup or getattr(chat, "gigagroup", False):
                is_group = True
                chat_type = "group"
            else:
                is_group = False
                chat_type = "channel"
                can_post = False

            if hasattr(chat, "default_banned_rights") and chat.default_banned_rights:
                if chat.default_banned_rights.send_messages:
                    can_post = False

        elif isinstance(chat, Chat):
            is_group = True
            chat_type = "group"
        else:
            return None

        return {
            "id": chat.id,
            "title": title,
            "username": username,
            "peer": peer_str,
            "type": chat_type,
            "is_group": is_group,
            "can_post": can_post,
            "participants_count": participants_count,
            "matched_query": matched_query,
            "description": ""
        }

    @classmethod
    async def run_contextual_discovery(
        cls,
        user_query: str,
        search_id: str,
        max_total_candidates: int = 40
    ) -> List[Dict[str, Any]]:
        """
        Full End-to-End Contextual Discovery:
        AI Expansion -> Telegram Search -> Exclusion Filter -> Embeddings & Reranker -> Supabase Persistence.
        """
        logger.info(f"Starting contextual discovery for search #{search_id} with query: '{user_query}'")
        db.update_discovery_search(search_id, {"status": "searching"})

        # 1. Exclude already added chats
        excluded_peers = db.get_existing_peers_set()
        logger.info(f"Exclusion list contains {len(excluded_peers)} chats.")

        # 2. AI Query Expansion via Gemini Flash Cascade
        expansion = AIDiscoveryEngine.expand_query(user_query)
        intent = expansion.get("intent", "advertising_source")
        sub_queries = expansion.get("search_queries", [])
        db.update_discovery_search(search_id, {"intent": intent})
        db.save_discovery_queries(search_id, sub_queries)

        # 3. Compute Query Embedding via Gemini
        query_embedding = AIDiscoveryEngine.get_embedding(user_query)

        # 4. Multi-query Telegram Search
        raw_candidates_map = {}
        queries_to_run = sub_queries[:6] if sub_queries else [{"text": user_query, "type": "primary"}]

        for q_obj in queries_to_run:
            q_text = q_obj.get("text", user_query)
            logger.info(f"Searching Telegram for sub-query: '{q_text}'...")
            found = await cls.search_single_query(q_text, limit=15, excluded_peers=excluded_peers)
            for c in found:
                if c["peer"].lower() not in raw_candidates_map:
                    raw_candidates_map[c["peer"].lower()] = c
            
            # Anti-flood gentle pause between Telegram search queries
            await asyncio.sleep(0.6)

        raw_candidates = list(raw_candidates_map.values())
        logger.info(f"Collected {len(raw_candidates)} unique unadded candidates.")
        db.update_discovery_search(search_id, {
            "status": "scoring",
            "total_candidates": len(raw_candidates)
        })

        if not raw_candidates:
            db.update_discovery_search(search_id, {
                "status": "completed",
                "total_candidates": 0,
                "total_relevant": 0
            })
            return []

        # 5. Batch Qualification & Vector Scoring via Gemini
        scored_candidates = AIDiscoveryEngine.batch_qualify_candidates(
            user_query=user_query,
            query_embedding=query_embedding,
            candidates=raw_candidates
        )

        # 6. Save ranked results to Supabase
        relevant_count = sum(1 for c in scored_candidates if c.get("final_score", 0) >= 60)
        db.save_discovery_results(search_id, scored_candidates)
        db.update_discovery_search(search_id, {
            "status": "completed",
            "total_relevant": relevant_count
        })

        logger.info(f"Contextual discovery #{search_id} completed: {len(scored_candidates)} candidates scored ({relevant_count} highly relevant).")
        return scored_candidates
