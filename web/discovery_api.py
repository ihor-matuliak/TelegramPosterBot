"""Discovery API, separate from posting and account management."""

import asyncio
import random
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from core.client import client
from core.discovery import DiscoveryManager
from core.finder import ChatFinder
from database.client import db
from database.discovery import ACTIVE_STATUSES, DiscoveryRepository, DiscoveryStorageError

router = APIRouter(prefix="/api/discovery")
repository = DiscoveryRepository(db.client)
manager = DiscoveryManager(repository, client)
Decision = Literal["accepted", "unverified", "rejected", "below_minimum", "existing", "all"]


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1500)
    min_members: int = Field(default=0, ge=0, le=100000000)


class AddRequest(BaseModel):
    result_id: UUID | None = None
    chat_peer: str = Field(min_length=1, max_length=200)
    interval_minutes: int = Field(default=1440, ge=1)
    title: str = ""
    post_id: UUID | None = None


class BatchRequest(BaseModel):
    items: list[AddRequest] = Field(min_length=1, max_length=50)


class HideRequest(BaseModel):
    result_id: UUID


@router.post("/search")
async def start_search(req: SearchRequest) -> dict:
    query = req.query.strip()
    if not query:
        raise HTTPException(400, "Введіть пошукові слова.")
    try:
        session = await manager.start(query, req.min_members)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"search_id": session["id"], "status": session["status"]}


@router.get("/active")
async def active_search() -> dict:
    return {"search": await repository.active(), "error": manager.runtime_error}


@router.post("/search/{search_id}/stop")
async def stop_search(search_id: UUID) -> dict:
    try:
        await manager.stop(str(search_id))
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"status": "stopped", "search_id": str(search_id)}


@router.get("/results/{search_id}")
async def results(search_id: UUID, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100),
                  decision: Decision = "accepted") -> dict:
    session = await repository.session(str(search_id))
    if not session:
        raise HTTPException(404, "Сесію не знайдено.")
    page = await repository.results(str(search_id), offset, limit, decision)
    return {**page, "search": session, "is_completed": session["status"] not in ACTIVE_STATUSES,
            "error": manager.runtime_error if manager.search_id == str(search_id) else None}


@router.get("/history")
async def history(offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100),
                  decision: Decision = "all", include_hidden: bool = False) -> dict:
    return await repository.results(None, offset, limit, decision, include_hidden)


@router.post("/add-to-posting")
async def add_to_posting(req: AddRequest) -> dict:
    result = await ChatFinder.join_and_add_chat(req.chat_peer, req.interval_minutes, req.title,
                                              str(req.post_id) if req.post_id else None)
    if result["status"] == "ok" and req.result_id:
        await repository.set_result_status(str(req.result_id), "added_to_posting")
    return result


@router.post("/batch-add")
async def batch_add(req: BatchRequest) -> dict:
    added = 0
    errors = []
    for index, item in enumerate(req.items):
        if index:
            await asyncio.sleep(random.uniform(15, 35))
        result = await add_to_posting(item)
        if result["status"] == "ok":
            added += 1
        else:
            errors.append({"peer": item.chat_peer, "message": result["message"]})
        if result.get("retry_after"):
            break
    return {"status": "ok", "added_count": added, "errors": errors}


@router.post("/hide")
async def hide(req: HideRequest) -> dict:
    await repository.set_result_status(str(req.result_id), "hidden")
    return {"status": "ok"}
