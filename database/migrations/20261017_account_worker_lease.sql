-- Exclusive use of additional account sessions (2026-10-17)
-- Additive migration. Run once in Supabase SQL Editor; safe to run again.
--
-- Telegram permanently revokes a session (AuthKeyDuplicatedError) when it is used
-- from two IP addresses at the same time: e.g. the server plus a local copy, or the
-- old and new container overlapping during a redeploy. A process now has to hold a
-- short lease on an account (renewed every 15 s) before it connects its session.
BEGIN;
ALTER TABLE public.accounts
    ADD COLUMN IF NOT EXISTS worker_id TEXT,                  -- Process that currently owns the session
    ADD COLUMN IF NOT EXISTS worker_host TEXT,                -- Host name of that process (shown in UI/bot)
    ADD COLUMN IF NOT EXISTS worker_heartbeat_at TIMESTAMPTZ; -- Last lease renewal
COMMIT;
NOTIFY pgrst, 'reload schema';
