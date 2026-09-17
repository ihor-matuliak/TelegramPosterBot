"""Strict asynchronous discovery storage with database-enforced uniqueness."""

import asyncio
from datetime import datetime, timezone
from typing import Any, Callable

from core.discovery_relevance import normalize_peer

ACTIVE_STATUSES = ["pending", "searching", "scoring", "waiting", "stopping"]


class DiscoveryStorageError(RuntimeError):
    pass


class DiscoveryRepository:
    def __init__(self, client: Any) -> None:
        self.client = client

    async def execute(self, operation: Callable[[], Any]) -> Any:
        if self.client is None:
            raise DiscoveryStorageError("Supabase не налаштовано.")
        task = asyncio.create_task(asyncio.to_thread(operation))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # Do not abandon an in-flight insert and race it against the next session.
            await task
            raise
        except Exception as exc:
            raise DiscoveryStorageError(
                "Помилка Supabase. Перевірте з’єднання та міграцію 20260916_continuous_discovery.sql."
            ) from exc

    async def preflight(self) -> None:
        await self.execute(lambda: self.client.table("discovery_searches").select("id,min_members,worker_version").limit(1).execute())
        await self.execute(lambda: self.client.table("discovery_results").select("id,telegram_id,decision,evidence").limit(1).execute())
        await self.execute(lambda: self.client.table("discovery_queries").select("id,cursor").limit(1).execute())

    async def active(self) -> dict[str, Any] | None:
        res = await self.execute(lambda: self.client.table("discovery_searches").select("*").in_("status", ACTIVE_STATUSES).order("created_at", desc=True).limit(1).execute())
        return next(iter(res.data), None)

    async def create(self, query: str, min_members: int) -> dict[str, Any]:
        res = await self.execute(lambda: self.client.table("discovery_searches").insert({
            "query": query, "min_members": min_members, "status": "pending",
            "intent": "reddit_ofm_b2b", "worker_version": 1,
        }).execute())
        if not res.data:
            raise DiscoveryStorageError("Supabase не підтвердив створення пошуку.")
        return res.data[0]

    async def update(self, search_id: str, **fields: Any) -> None:
        fields["updated_at"] = datetime.now(timezone.utc).isoformat()
        res = await self.execute(lambda: self.client.table("discovery_searches").update(fields).eq("id", search_id).execute())
        if not res.data:
            raise DiscoveryStorageError("Сесію пошуку не знайдено або зміни не збережено.")

    async def session(self, search_id: str) -> dict[str, Any] | None:
        res = await self.execute(lambda: self.client.table("discovery_searches").select("*").eq("id", search_id).limit(1).execute())
        return next(iter(res.data), None)

    async def queries(self, search_id: str) -> list[str]:
        res = await self.execute(lambda: self.client.table("discovery_queries").select("query_text").eq("search_id", search_id).order("created_at").limit(150).execute())
        return list(dict.fromkeys(row["query_text"] for row in res.data))

    async def save_queries(self, search_id: str, queries: list[str]) -> None:
        if queries:
            await self.execute(lambda: self.client.table("discovery_queries").insert([
                {"search_id": search_id, "query_text": q, "query_type": "related"} for q in queries
            ]).execute())

    async def cursor(self, search_id: str, query: str) -> dict[str, Any]:
        res = await self.execute(lambda: self.client.table("discovery_queries").select("cursor").eq("search_id", search_id).eq("query_text", query).limit(1).execute())
        return res.data[0].get("cursor") or {} if res.data else {}

    async def save_cursor(self, search_id: str, query: str, cursor: dict[str, Any]) -> None:
        await self.execute(lambda: self.client.table("discovery_queries").update({"cursor": cursor}).eq("search_id", search_id).eq("query_text", query).execute())

    async def existing_peers(self) -> set[str]:
        peers: set[str] = set()
        offset = 0
        while True:
            res = await self.execute(lambda: self.client.table("chats").select("chat_peer").order("id").range(offset, offset + 499).execute())
            peers.update(normalize_peer(row["chat_peer"]) for row in res.data)
            if len(res.data) < 500:
                return peers
            offset += 500

    async def known(self, telegram_id: int, peer: str) -> bool:
        res = await self.execute(lambda: self.client.table("discovery_results").select("id").eq("telegram_id", telegram_id).limit(1).execute())
        if res.data:
            return True
        # Backfill stable IDs for old results, preserving hidden/added statuses.
        res = await self.execute(lambda: self.client.table("discovery_results").select("id").is_("telegram_id", "null").ilike("telegram_peer", peer.replace("_", "\\_")).order("created_at").limit(1).execute())
        if res.data:
            await self.execute(lambda: self.client.table("discovery_results").update({"telegram_id": telegram_id}).eq("id", res.data[0]["id"]).execute())
            return True
        return False

    @staticmethod
    def candidate_row(search_id: str, candidate: dict[str, Any]) -> dict[str, Any]:
        row = {key: candidate.get(key) for key in (
            "telegram_id", "title", "description", "decision", "evidence_status", "evidence", "topic",
            "ad_policy", "posting_access", "last_message_at", "assessed_by", "relevance_score",
            "promo_score", "activity_score", "final_score", "classification", "ai_reason", "matched_query",
        )}
        row.update(search_id=search_id, telegram_peer=candidate["peer"].lower(), chat_type=candidate["type"],
                   members_count=candidate.get("participants_count"), can_post=candidate.get("can_post", False))
        return row

    async def save_candidate(self, search_id: str, candidate: dict[str, Any]) -> bool:
        row = self.candidate_row(search_id, candidate)
        res = await self.execute(lambda: self.client.table("discovery_results").upsert(
            row, on_conflict="telegram_id", ignore_duplicates=True,
        ).execute())
        return bool(res.data)

    async def update_candidate(self, search_id: str, candidate: dict[str, Any]) -> None:
        row = self.candidate_row(search_id, candidate)
        res = await self.execute(lambda: self.client.table("discovery_results").update(row).eq(
            "telegram_id", candidate["telegram_id"]).eq("search_id", search_id).execute())
        if not res.data:
            raise DiscoveryStorageError("Supabase не підтвердив збереження перевірки чату.")

    async def pending(self, search_id: str) -> list[dict[str, Any]]:
        res = await self.execute(lambda: self.client.table("discovery_results").select("*").eq(
            "search_id", search_id).eq("assessed_by", "pending").order("created_at").limit(50).execute())
        return [{**row, "peer": row["telegram_peer"], "type": row["chat_type"],
                 "participants_count": row["members_count"], "messages": []} for row in res.data]

    async def results(self, search_id: str | None = None, offset: int = 0, limit: int = 50,
                      decision: str = "accepted", include_hidden: bool = False) -> dict[str, Any]:
        def query() -> Any:
            q = self.client.table("discovery_results").select("*", count="exact")
            if search_id:
                q = q.eq("search_id", search_id)
            if decision != "all":
                q = q.eq("decision", decision)
            if not include_hidden:
                q = q.neq("status", "hidden")
            q = q.order("created_at", desc=not bool(search_id)).order("id", desc=not bool(search_id))
            return q.range(offset, offset + limit - 1).execute()
        res = await self.execute(query)
        return {"results": res.data, "total": res.count or 0, "offset": offset, "limit": limit}

    async def set_result_status(self, result_id: str, status: str) -> None:
        res = await self.execute(lambda: self.client.table("discovery_results").update({"status": status}).eq("id", result_id).execute())
        if not res.data:
            raise DiscoveryStorageError("Результат не знайдено.")
