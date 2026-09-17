# Professional Telegram discovery

Approved by the user on 2026-09-16. Audience: agencies, OFM managers,
chatters and adult traffic professionals buying Reddit accounts and model promotion.

## Behaviour

- A single server-owned session runs until Stop; closing the browser does not stop it.
- Search terms combine user seeds, a curated multilingual vocabulary and bounded AI
  expansion. Exhausted cycles wait before searching again; Telegram FloodWait wins.
- Public groups are qualified using their description and accessible recent messages.
  Adult fan/content groups and unrelated Reddit/traffic groups are rejected.
- Relevance, evidence availability, posting permissions and advertising rules remain
  separate facts. Unknown membership counts and permissions are not invented.
- Every discovered public chat is persisted with a stable Telegram peer ID, first
  search, decision and evidence. Later sessions exclude previous discoveries, including
  hidden/rejected results. History is paginated and remains accessible.
- Minimum members is applied after enrichment; unknown counts do not pass a positive
  threshold. Unverified groups are available separately, never labelled qualified.
- Results are persisted progressively. Stop interrupts network work and waits safely
  for any outstanding database write. Failures are visible, never reported as empty success.
- Single Python process / one ASGI worker is supported. On process restart an unfinished
  session resumes from persisted queries/results; Stop remains terminal.

## Implementation plan

1. Add an additive, rerunnable Supabase migration and strict async discovery repository.
2. Add pure evidence-based qualification and multilingual query planning with tests.
3. Replace legacy AI SDK with maintained async Gemini client, bounded timeouts,
   validated JSON, explicit fallback and a quota cooldown.
4. Implement a cancellable Telegram search/enrichment adapter and session worker,
   paced RPC calls, paginated message search, progressive persistence and recovery.
5. Wire lifecycle/API start, stop, active session, paginated results and history.
6. Update the existing dark UI with member filter, live state, history and safe cards.
7. Verify relevance, deduplication, pagination, cancellation, failure handling and UI
   contracts with isolated tests. Live checks must not start posting or join groups.

## Acceptance examples

- “OnlyFans agency: Reddit traffic and account suppliers” qualifies.
- “Reddit memes”, “football fans”, “road traffic” and fan-only adult feeds do not.
- Repeating a search or renaming a discovered group never creates a new result.
- Database failures surface as errors; no successful result exists only in memory.
- Existing post selection is forwarded when manually adding a result to posting.

## Deployment

Apply database/migrations/20260916_continuous_discovery.sql before starting the new
worker. Secrets stay in environment variables. A database preflight explains a missing
migration rather than starting an unreliable search. Telegram API visibility limits
the available groups; continuous execution cannot guarantee continuous new results.
