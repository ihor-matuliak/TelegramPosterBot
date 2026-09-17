"""Read-only Telegram smoke check with an in-memory copy of the existing session."""

import asyncio
import os
import sqlite3
import sys
from pathlib import Path
from types import ModuleType

from dotenv import load_dotenv
from telethon import TelegramClient, errors
from telethon.crypto import AuthKey
from telethon.sessions import MemorySession, StringSession
from telethon.tl.functions.contacts import SearchRequest
from telethon.tl.types import Channel

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


async def main() -> None:
    token = os.getenv("TELEGRAM_STRING_SESSION", "").strip()
    session = StringSession(token) if token else MemorySession()
    if not token:
        name = os.getenv("TELEGRAM_SESSION_NAME", "poster_session")
        path = ROOT / (name if name.endswith(".session") else name + ".session")
        if not path.exists():
            print("Telegram session: missing")
            return
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as source:
            row = source.execute("SELECT dc_id, server_address, port, auth_key FROM sessions LIMIT 1").fetchone()
        if not row or not row[3]:
            print("Telegram session: no authorization key")
            return
        session.set_dc(row[0], row[1], row[2])
        session.auth_key = AuthKey(row[3])
    client = TelegramClient(session, int(os.environ["TELEGRAM_API_ID"]), os.environ["TELEGRAM_API_HASH"],
                            flood_sleep_threshold=0, connection_retries=0, request_retries=0, timeout=15)
    try:
        await asyncio.wait_for(client.connect(), 20)
        authorized = await asyncio.wait_for(client.is_user_authorized(), 20)
        print("Telegram authorized:", authorized)
        if authorized:
            result = await asyncio.wait_for(client(SearchRequest(q="OnlyFans", limit=30)), 25)
            groups = [c for c in result.chats if isinstance(c, Channel) and c.megagroup and c.username]
            print("Directory results:", len(result.chats))
            print("Public groups:", len(groups))
            if "--full" in sys.argv:
                sys.path.insert(0, str(ROOT))
                # Reuse the in-memory client, never open the production session for writing.
                module = ModuleType("core.client")
                module.client = client
                sys.modules["core.client"] = module
                from core.finder import TelegramDiscovery
                from core.ai_discovery import AIDiscoveryEngine
                async def wait_notice(seconds: int, message: str) -> None:
                    print("Rate-limit wait seconds:", seconds)
                    raise RuntimeError("Stop smoke test rather than wait or retry")
                transport = TelegramDiscovery(client, wait_notice)
                await asyncio.sleep(5)
                pages = 0
                async for found, cursor in transport.search("OnlyFans", {}):
                    pages += 1
                    print("Search page:", pages, "candidates:", len(found), "cursor:", cursor is not None)
                    if pages >= 2:
                        break
                if groups:
                    row = transport.parse(groups[0], "OnlyFans")
                    row = await transport.enrich(row)
                    print("Description available:", bool(row["description"]))
                    print("Messages sampled:", len(row["messages"]))
                    ai = AIDiscoveryEngine()
                    try:
                        result = await ai.assess(row, 0)
                        print("Qualification:", result["decision"], "engine:", result["assessed_by"])
                    finally:
                        await ai.close()
    except errors.FloodWaitError as exc:
        print("Telegram FloodWait seconds:", exc.seconds)
    except Exception as exc:
        print("Telegram check:", type(exc).__name__)
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
