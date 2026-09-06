# Telegram Auto-Poster System Rules & Architecture

## 1. Project Overview & Goal
* **Goal**: Automated Telegram posting system for ~50 groups/channels with custom posting intervals (e.g., 60m, 24h, 3 days), emojis/text formatted ads, anti-ban protections, dual interfaces (Telegram-styled Desktop Web UI on PC + mobile Admin Telegram Bot on phone), with Supabase as the single source of truth cloud database.
* **Core Engine**: Telethon (MTProto client running on behalf of the user's Telegram account).
* **Database**: Supabase (PostgreSQL).
* **Desktop UI**: FastAPI + Vanilla HTML/CSS/JS meticulously styled to match the official **Telegram Desktop / Telegram Web K** design system.
* **Mobile Admin**: aiogram 3.x with inline buttons, states, notifications, and instant pause/resume.

---

## 2. Code Quality & Architecture Rules
1. **Modularity & Separation of Concerns**:
   - `core/`: MTProto userbot, scheduler, rate limiter, jitter logic, and flood/slowmode exception handlers.
   - `database/`: Supabase client and query helpers (clean CRUD operations for chats, posts, settings, logs).
   - `web/`: FastAPI endpoints and static assets for Desktop Web UI.
   - `bot/`: aiogram 3 handlers, inline keyboards, routers, and admin notifications.
   - `config.py`: Centralized type-safe settings loaded from `.env`.
   - `main.py`: Async lifecycle entrypoint orchestrating the Userbot worker, FastAPI web server, and aiogram bot concurrently.

2. **Clean Code & Type Hinting**:
   - Always use Python type hints (`str`, `int`, `Optional`, `dict`, `list`).
   - Use `asyncio` for non-blocking I/O across the board.
   - Consistent logging with timestamp, level, and informative context (e.g. `logger.info`, `logger.warning`, `logger.error`).

3. **Telegram Rate Limiting & Anti-Ban Strict Principles**:
   - **Slowmode Handling**: Always catch `SlowmodeWaitError` / slowmode limits, calculate remaining seconds, and delay posting for that specific chat.
   - **FloodWait Handling**: Catch `FloodWaitError`, log time, sleep asynchronously for the required seconds + small margin, and resume.
   - **Jitter & Randomization**: Add randomized delays (e.g., interval ± 2-5 minutes, and 15-35s pause between consecutive chats) to simulate realistic human behavior.
   - **No aggressive loops**: Prevent multiple rapid retries upon failures; log errors and schedule for the next cycle.

4. **UI/UX Design Standards (Telegram Desktop Design System)**:
   - Match official Telegram Desktop / Web K Dark Mode theme:
     - Background: `#0e1621`
     - Panels & Cards: `#17212b`
     - Interactive Inputs & Elements: `#242f3d`
     - Telegram Primary Accent: `#2481cc` / `#2ea6ff`
     - Text colors: Primary `#ffffff`, Secondary `#7f91a4`
   - Include interactive Telegram Message Preview with authentic bubble styling, timestamp, and read ticks `✓✓`.
   - Responsive, clean layout without bulky third-party frontend dependencies (pure Vanilla CSS/JS for maximum speed and zero build step).

5. **Supabase Schema Consistency**:
   - Maintain schema in `database/schema.sql`.
   - All tables (`chats`, `posts`, `settings`, `logs`) must have appropriate indexes, primary keys, and timestamp defaults.
