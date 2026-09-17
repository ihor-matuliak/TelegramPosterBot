-- Additive migration. Run once in Supabase SQL Editor; safe to run again.
BEGIN;
ALTER TABLE public.discovery_queries ADD COLUMN IF NOT EXISTS cursor JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE public.discovery_searches
    ADD COLUMN IF NOT EXISTS min_members INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS current_query TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS cycle INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS queries_run INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS progress_message TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS ai_status TEXT NOT NULL DEFAULT 'pending',
    ADD COLUMN IF NOT EXISTS next_request_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ADD COLUMN IF NOT EXISTS worker_version INTEGER;

-- Only retire sessions created by the old, non-resumable implementation.
UPDATE public.discovery_searches
SET status = 'failed', error_message = 'Перервано старою версією. Запустіть новий пошук.',
    completed_at = NOW()
WHERE worker_version IS NULL AND status IN ('pending', 'searching', 'scoring');

CREATE UNIQUE INDEX IF NOT EXISTS discovery_one_active_session
ON public.discovery_searches ((1))
WHERE status IN ('pending', 'searching', 'scoring', 'waiting', 'stopping');

ALTER TABLE public.discovery_results
    ADD COLUMN IF NOT EXISTS telegram_id BIGINT,
    ADD COLUMN IF NOT EXISTS decision TEXT NOT NULL DEFAULT 'accepted',
    ADD COLUMN IF NOT EXISTS evidence_status TEXT NOT NULL DEFAULT 'legacy',
    ADD COLUMN IF NOT EXISTS evidence JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS topic TEXT NOT NULL DEFAULT 'unverified',
    ADD COLUMN IF NOT EXISTS ad_policy TEXT NOT NULL DEFAULT 'unknown',
    ADD COLUMN IF NOT EXISTS posting_access TEXT NOT NULL DEFAULT 'unknown',
    ADD COLUMN IF NOT EXISTS last_message_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS assessed_by TEXT NOT NULL DEFAULT 'legacy';
ALTER TABLE public.discovery_results ALTER COLUMN members_count DROP NOT NULL;
ALTER TABLE public.discovery_results ALTER COLUMN members_count DROP DEFAULT;
CREATE UNIQUE INDEX IF NOT EXISTS discovery_unique_telegram_id
ON public.discovery_results (telegram_id);
CREATE INDEX IF NOT EXISTS discovery_peer_normalized
ON public.discovery_results (lower(telegram_peer));
CREATE INDEX IF NOT EXISTS discovery_history_page
ON public.discovery_results (created_at DESC, id DESC);

COMMIT;
NOTIFY pgrst, 'reload schema';
