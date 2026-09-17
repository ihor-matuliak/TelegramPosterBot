"""Read-only service checks. Prints statuses, never credentials or account data."""

import asyncio
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


async def main() -> None:
    async with httpx.AsyncClient(timeout=20) as client:
        url, key = os.getenv("SUPABASE_URL", ""), os.getenv("SUPABASE_KEY", "")
        if url and key:
            for table, columns in [("discovery_searches", "id,min_members,worker_version"),
                                   ("discovery_results", "id,telegram_id,decision"),
                                   ("discovery_queries", "id,cursor")]:
                try:
                    response = await client.get(f"{url}/rest/v1/{table}",
                        headers={"apikey": key, "Authorization": f"Bearer {key}"},
                        params={"select": columns, "limit": 0})
                    print(f"{table}: HTTP {response.status_code}")
                    if response.status_code >= 400:
                        print("  error_code:", response.json().get("code", "unknown"))
                except Exception as exc:
                    print(f"{table}: {type(exc).__name__}")
        else:
            print("Supabase: not configured")
        ai_key = os.getenv("GEMINI_API_KEY", "")
        if ai_key:
            try:
                response = await client.get("https://generativelanguage.googleapis.com/v1beta/models",
                                            headers={"x-goog-api-key": ai_key})
                print(f"Gemini models: HTTP {response.status_code}")
                if response.is_success:
                    model = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
                    names = {item["name"] for item in response.json().get("models", [])}
                    print("Configured model available:", f"models/{model}" in names)
            except Exception as exc:
                print("Gemini:", type(exc).__name__)
        else:
            print("Gemini: not configured (rule fallback)")


if __name__ == "__main__":
    asyncio.run(main())
