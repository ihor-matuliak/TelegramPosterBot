-- Store the chat -> post assignment in the database (2026-10-17)
-- Additive migration. Run once in Supabase SQL Editor; safe to run again.
--
-- The production chats table never received this column, so assignments were kept
-- only in data/chat_posts.json on the server. That file is reset to the committed
-- version on every redeploy, so assignments made in the UI/bot were silently lost.
-- Old and new application versions both work with and without this column.
ALTER TABLE public.chats ADD COLUMN IF NOT EXISTS post_id UUID REFERENCES public.posts(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_chats_post_id ON public.chats(post_id);
NOTIFY pgrst, 'reload schema';
