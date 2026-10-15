-- Multiple Telegram posting accounts (2026-10-15)
-- Additive migration. Run once in Supabase SQL Editor; safe to run again.
--
-- Production safety:
--   * Existing rows are not modified. Chats/logs with account_id = NULL keep
--     belonging to the primary account (TELEGRAM_STRING_SESSION / session file).
--   * The old application version ignores the new table and columns.
--   * BUT the old version posts every active chat with the primary account.
--     Deploy the new application version BEFORE adding chats to new accounts.
BEGIN;

CREATE TABLE IF NOT EXISTS public.accounts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    label TEXT NOT NULL DEFAULT '',                 -- Human readable name shown in UI/bot
    telegram_user_id BIGINT UNIQUE,                 -- Telegram user id (prevents duplicates)
    first_name TEXT DEFAULT NULL,
    username TEXT DEFAULT NULL,
    phone TEXT DEFAULT NULL,
    is_premium BOOLEAN NOT NULL DEFAULT FALSE,
    session_string TEXT NOT NULL,                   -- Telethon StringSession (secret!)
    posting_enabled BOOLEAN NOT NULL DEFAULT FALSE, -- Per-account posting switch
    status TEXT NOT NULL DEFAULT 'active',          -- 'active', 'unauthorized', 'error'
    last_error TEXT DEFAULT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- NULL = primary account. Deleting an account removes its own chat list.
ALTER TABLE public.chats
    ADD COLUMN IF NOT EXISTS account_id UUID REFERENCES public.accounts(id) ON DELETE CASCADE;
-- No foreign key: history must survive account deletion without being
-- re-attributed to the primary account (which would skew its rate limits).
ALTER TABLE public.logs ADD COLUMN IF NOT EXISTS account_id UUID;

CREATE INDEX IF NOT EXISTS idx_chats_account ON public.chats(account_id);
CREATE INDEX IF NOT EXISTS idx_logs_account_status ON public.logs(account_id, status, created_at DESC);

ALTER TABLE public.accounts ENABLE ROW LEVEL SECURITY;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'Allow full access to accounts') THEN
        CREATE POLICY "Allow full access to accounts" ON public.accounts FOR ALL USING (true) WITH CHECK (true);
    END IF;
END $$;

COMMIT;
NOTIFY pgrst, 'reload schema';
