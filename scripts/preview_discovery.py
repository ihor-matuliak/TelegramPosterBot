"""UI fixture server. No imports of application credentials or Telegram clients."""

from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI()
static = Path(__file__).resolve().parents[1] / "web" / "static"
app.mount("/static", StaticFiles(directory=static), name="static")
session = {"id": str(uuid4()), "query": "OnlyFans, OFM, Reddit", "min_members": 100,
           "status": "searching", "total_candidates": 6, "total_relevant": 2, "queries_run": 4,
           "ai_status": "ready", "current_query": "reddit onlyfans", "progress_message": "Шукаємо нові групи…"}
rows = [{"id": str(uuid4()), "title": "OFM Agency's Chat — Reddit Traffic", "telegram_peer": "@example_ofm",
         "chat_type": "group", "members_count": 1850, "status": "new", "decision": "accepted",
         "description": "Професійна спільнота агенцій і спеціалістів з Reddit-трафіку.",
         "posting_access": "join_required", "ad_policy": "unknown", "assessed_by": "ai",
         "ai_reason": "Обговорюють Reddit-акаунти та просування моделей OnlyFans.",
         "evidence": ["Looking for Reddit account suppliers for our OnlyFans agency."]}]


@app.get("/")
async def index(): return FileResponse(static / "index.html")


@app.get("/api/discovery/active")
async def active(): return {"search": session}


@app.get("/api/discovery/results/{sid}")
async def results(sid: str): return {"search": session, "results": rows, "total": len(rows), "is_completed": session["status"] == "stopped"}


@app.get("/api/discovery/history")
async def history(): return {"results": rows, "total": len(rows)}


@app.post("/api/discovery/search/{sid}/stop")
async def stop(sid: str):
    session.update(status="stopped", progress_message="Пошук зупинено. Результати збережено.")
    return {"status": "stopped"}


@app.post("/api/discovery/search")
async def start():
    session.update(status="searching", progress_message="Шукаємо нові групи…")
    return {"search_id": session["id"], "status": "searching"}


@app.get("/api/status")
async def status(): return {"is_authorized": False, "is_running": False, "settings": {}}


@app.get("/api/{resource}")
async def empty(resource: str): return []
