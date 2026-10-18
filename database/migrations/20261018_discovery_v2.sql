-- Discovery v2: more sources, multilingual ranking, one worker per session (2026-10-18)
-- Additive migration. Run once in Supabase SQL Editor; safe to run again.
-- Existing results are not modified. Posting does not depend on these tables.
BEGIN;

ALTER TABLE public.discovery_results
    ADD COLUMN IF NOT EXISTS language TEXT,                    -- 'en', 'es', 'pt', 'de', 'fr', 'it', 'ru', 'uk', 'other'
    ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'search', -- 'search', 'mention', 'forward', 'linked', 'similar'
    ADD COLUMN IF NOT EXISTS source_peer TEXT,                 -- chat/channel through which it was found
    ADD COLUMN IF NOT EXISTS linked_peer TEXT,                 -- channel: its discussion group
    ADD COLUMN IF NOT EXISTS ad_signals INTEGER;               -- % of recent messages that look like ads

-- Adaptive query order: productive queries first, exhausted ones skipped.
ALTER TABLE public.discovery_queries
    ADD COLUMN IF NOT EXISTS found_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS accepted_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS empty_cycles INTEGER NOT NULL DEFAULT 0;

-- One process runs a session even when a local copy shares this database.
ALTER TABLE public.discovery_searches
    ADD COLUMN IF NOT EXISTS worker_id TEXT,
    ADD COLUMN IF NOT EXISTS worker_heartbeat_at TIMESTAMPTZ;

-- Snowball leads: @usernames / t.me links mentioned in relevant groups, resolved slowly.
CREATE TABLE IF NOT EXISTS public.discovery_leads (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    search_id UUID REFERENCES public.discovery_searches(id) ON DELETE SET NULL,
    username TEXT NOT NULL,
    username_key TEXT NOT NULL UNIQUE,                         -- lower(username): each lead is resolved once, ever
    source TEXT NOT NULL DEFAULT 'mention',
    source_peer TEXT,
    status TEXT NOT NULL DEFAULT 'pending',                    -- 'pending', 'done', 'failed'
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS discovery_leads_pending ON public.discovery_leads (status, created_at);
CREATE INDEX IF NOT EXISTS discovery_results_rank ON public.discovery_results (search_id, decision, final_score DESC);

ALTER TABLE public.discovery_leads ENABLE ROW LEVEL SECURITY;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'Allow full access to discovery_leads') THEN
        CREATE POLICY "Allow full access to discovery_leads" ON public.discovery_leads FOR ALL USING (true) WITH CHECK (true);
    END IF;
END $$;

COMMIT;
NOTIFY pgrst, 'reload schema';
