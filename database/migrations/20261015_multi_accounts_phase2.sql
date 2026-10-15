-- OPTIONAL phase 2: allow the same chat in the lists of different accounts.
-- Run ONLY after 20261015_multi_accounts.sql AND after every running server
-- uses the multi-account version. Older versions upsert on chat_peer alone and
-- would fail to add chats once this constraint is gone.
-- Requires PostgreSQL 15+ (NULLS NOT DISTINCT), which Supabase provides.
BEGIN;
ALTER TABLE public.chats DROP CONSTRAINT IF EXISTS chats_chat_peer_key;
CREATE UNIQUE INDEX IF NOT EXISTS chats_account_peer_unique
ON public.chats (account_id, chat_peer) NULLS NOT DISTINCT;
COMMIT;
NOTIFY pgrst, 'reload schema';
