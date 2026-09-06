# Telegram Auto-Poster System

## Project Goal
Automated Telegram sales ads posting system for ~50 groups with custom per-chat intervals, anti-ban protections, dual control interfaces (Desktop Web UI in Telegram style + Mobile Admin Bot in Telegram), and Supabase as the central database.

## Tech Stack
* **Language**: Python 3.10+ (Asyncio)
* **MTProto Client (Userbot)**: Telethon
* **Admin Bot (Mobile)**: aiogram 3.x
* **Database**: Supabase (PostgreSQL) via `supabase-py`
* **Desktop UI**: FastAPI + Vanilla HTML/CSS/JS (Authentic Telegram Desktop Dark Theme)

## Key Guidelines
* Follow `.agents/rules/project_rules.md` strictly for code style, rate limiting, and design tokens.
* Keep secrets safe in `.env` (never commit `.env` or `*.session` files).
