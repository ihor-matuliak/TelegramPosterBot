-- ==========================================================
-- TELEGRAM AUTO-POSTER & CONTEXTUAL DISCOVERY DATABASE SCHEMA
-- ==========================================================

-- 1. Table for Post Templates (Must be created before chats for Foreign Key reference)
CREATE TABLE IF NOT EXISTS public.posts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title TEXT NOT NULL DEFAULT 'Головне оголошення',
    content TEXT NOT NULL,                   -- Text with emojis & HTML tags
    is_active BOOLEAN NOT NULL DEFAULT TRUE, -- Currently selected active ad (Default fallback)
    source_msg_id INTEGER DEFAULT NULL,      -- Exact Telegram message ID in Saved Messages for 100% native clone
    source_chat_peer TEXT DEFAULT 'me',      -- 'me' (Saved Messages) or bot chat
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 2. Table for Posting Target Chats
CREATE TABLE IF NOT EXISTS public.chats (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    chat_peer TEXT UNIQUE NOT NULL,          -- '@channel_username', '-1001234567890', or invite link
    title TEXT DEFAULT NULL,                 -- Human readable chat title
    interval_minutes INTEGER NOT NULL DEFAULT 60, -- Posting interval in minutes
    post_id UUID REFERENCES public.posts(id) ON DELETE SET NULL, -- Specific post variant (NULL = default active post)
    last_posted_at TIMESTAMPTZ DEFAULT NULL, -- Timestamp of last successful post
    next_post_at TIMESTAMPTZ DEFAULT NOW(),  -- Next scheduled post time
    is_active BOOLEAN NOT NULL DEFAULT TRUE, -- Enable/disable posting for this chat
    status TEXT NOT NULL DEFAULT 'active',   -- 'active', 'slowmode_wait', 'restricted', 'error'
    last_error TEXT DEFAULT NULL,            -- Last error description
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Migration safety columns for existing databases
ALTER TABLE public.posts ADD COLUMN IF NOT EXISTS source_msg_id INTEGER DEFAULT NULL;
ALTER TABLE public.posts ADD COLUMN IF NOT EXISTS source_chat_peer TEXT DEFAULT 'me';
ALTER TABLE public.chats ADD COLUMN IF NOT EXISTS post_id UUID REFERENCES public.posts(id) ON DELETE SET NULL;

-- 3. Table for Global System Settings & Anti-Flood Limits
CREATE TABLE IF NOT EXISTS public.settings (
    id INTEGER PRIMARY KEY DEFAULT 1,
    is_running BOOLEAN NOT NULL DEFAULT FALSE,
    min_delay_seconds INTEGER NOT NULL DEFAULT 15,
    max_delay_seconds INTEGER NOT NULL DEFAULT 35,
    jitter_minutes INTEGER NOT NULL DEFAULT 3,

    -- Safety Limits
    max_posts_per_hour INTEGER NOT NULL DEFAULT 30,
    max_posts_per_day INTEGER NOT NULL DEFAULT 300,
    batch_size INTEGER NOT NULL DEFAULT 8,
    batch_rest_minutes INTEGER NOT NULL DEFAULT 8,

    -- Human Behavior & Fingerprint
    enable_typing_simulation BOOLEAN NOT NULL DEFAULT TRUE,
    typing_duration_seconds INTEGER NOT NULL DEFAULT 4,
    enable_anti_fingerprint BOOLEAN NOT NULL DEFAULT TRUE,
    enable_spintax BOOLEAN NOT NULL DEFAULT TRUE,

    -- Sleep Mode & Circuit Breaker
    enable_night_mode BOOLEAN NOT NULL DEFAULT FALSE,
    night_start_hour INTEGER NOT NULL DEFAULT 1,
    night_end_hour INTEGER NOT NULL DEFAULT 8,
    auto_circuit_breaker BOOLEAN NOT NULL DEFAULT TRUE,

    updated_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT single_row CHECK (id = 1)
);

-- 4. Table for Execution Logs & Audit Trail
CREATE TABLE IF NOT EXISTS public.logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    chat_peer TEXT NOT NULL,
    status TEXT NOT NULL,                    -- 'success', 'slowmode_wait', 'flood_wait', 'restricted', 'error'
    details TEXT DEFAULT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ==========================================================
-- CONTEXTUAL AI DISCOVERY & QUALIFICATION TABLES
-- ==========================================================

-- 5. Discovery Search Sessions
CREATE TABLE IF NOT EXISTS public.discovery_searches (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    query TEXT NOT NULL,
    intent TEXT DEFAULT 'advertising_source',
    status TEXT NOT NULL DEFAULT 'pending',   -- 'pending', 'searching', 'scoring', 'completed', 'failed'
    total_candidates INTEGER DEFAULT 0,
    total_relevant INTEGER DEFAULT 0,
    error_message TEXT DEFAULT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ DEFAULT NULL
);

-- 6. Generated Sub-Queries from Gemini
CREATE TABLE IF NOT EXISTS public.discovery_queries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    search_id UUID NOT NULL REFERENCES public.discovery_searches(id) ON DELETE CASCADE,
    query_text TEXT NOT NULL,
    query_type TEXT NOT NULL DEFAULT 'related', -- 'primary', 'related', 'promo', 'alias'
    priority INTEGER NOT NULL DEFAULT 1,
    results_count INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 7. Ranked Discovery Results / Candidates
CREATE TABLE IF NOT EXISTS public.discovery_results (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    search_id UUID NOT NULL REFERENCES public.discovery_searches(id) ON DELETE CASCADE,
    telegram_peer TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    chat_type TEXT NOT NULL DEFAULT 'group',  -- 'group', 'supergroup', 'channel'
    members_count INTEGER DEFAULT 0,
    can_post BOOLEAN NOT NULL DEFAULT TRUE,
    matched_query TEXT DEFAULT '',
    relevance_score INTEGER NOT NULL DEFAULT 0,
    promo_score INTEGER NOT NULL DEFAULT 0,
    activity_score INTEGER NOT NULL DEFAULT 0,
    final_score INTEGER NOT NULL DEFAULT 0,
    ai_reason TEXT DEFAULT '',
    classification TEXT NOT NULL DEFAULT 'relevant', -- 'highly_relevant', 'relevant', 'possibly_relevant', 'irrelevant'
    status TEXT NOT NULL DEFAULT 'new',       -- 'new', 'added_to_posting', 'hidden'
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ==========================================================
-- SEED DATA & ROW LEVEL SECURITY
-- ==========================================================

-- Seed default settings
INSERT INTO public.settings (
    id, is_running, min_delay_seconds, max_delay_seconds, jitter_minutes,
    max_posts_per_hour, max_posts_per_day, batch_size, batch_rest_minutes,
    enable_typing_simulation, typing_duration_seconds, enable_anti_fingerprint,
    enable_spintax, enable_night_mode, night_start_hour, night_end_hour,
    auto_circuit_breaker
)
VALUES (
    1, FALSE, 15, 35, 3,
    30, 300, 8, 8,
    TRUE, 4, TRUE,
    TRUE, FALSE, 1, 8,
    TRUE
)
ON CONFLICT (id) DO UPDATE SET
    max_posts_per_hour = EXCLUDED.max_posts_per_hour,
    max_posts_per_day = EXCLUDED.max_posts_per_day,
    batch_size = EXCLUDED.batch_size;

-- Seed default post
INSERT INTO public.posts (title, content, is_active)
SELECT 'Головне оголошення 📦', '🔥 Пропонуємо якісні товари за найкращими цінами!\n\n✨ Швидка відправка по всій Україні\n💬 Для замовлення пишіть в ПП', TRUE
WHERE NOT EXISTS (SELECT 1 FROM public.posts);

-- Enable RLS
ALTER TABLE public.chats ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.posts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.discovery_searches ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.discovery_queries ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.discovery_results ENABLE ROW LEVEL SECURITY;

DO $$ 
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'Allow full access to chats') THEN
        CREATE POLICY "Allow full access to chats" ON public.chats FOR ALL USING (true) WITH CHECK (true);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'Allow full access to posts') THEN
        CREATE POLICY "Allow full access to posts" ON public.posts FOR ALL USING (true) WITH CHECK (true);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'Allow full access to settings') THEN
        CREATE POLICY "Allow full access to settings" ON public.settings FOR ALL USING (true) WITH CHECK (true);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'Allow full access to logs') THEN
        CREATE POLICY "Allow full access to logs" ON public.logs FOR ALL USING (true) WITH CHECK (true);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'Allow full access to discovery_searches') THEN
        CREATE POLICY "Allow full access to discovery_searches" ON public.discovery_searches FOR ALL USING (true) WITH CHECK (true);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'Allow full access to discovery_queries') THEN
        CREATE POLICY "Allow full access to discovery_queries" ON public.discovery_queries FOR ALL USING (true) WITH CHECK (true);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'Allow full access to discovery_results') THEN
        CREATE POLICY "Allow full access to discovery_results" ON public.discovery_results FOR ALL USING (true) WITH CHECK (true);
    END IF;
END $$;

-- Indexes
CREATE INDEX IF NOT EXISTS idx_chats_next_post ON public.chats(next_post_at) WHERE is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_chats_post_id ON public.chats(post_id);
CREATE INDEX IF NOT EXISTS idx_logs_created_at ON public.logs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_discovery_results_search ON public.discovery_results(search_id, final_score DESC);
CREATE INDEX IF NOT EXISTS idx_discovery_results_peer ON public.discovery_results(telegram_peer);
