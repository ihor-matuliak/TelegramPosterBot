# Contextual Telegram Channel & Group Discovery Spec

## 1. Overview & Objectives
* **Goal**: Enable intelligent discovery and qualification of Telegram groups and channels based on contextual intent (natural language queries) rather than exact keyword matches alone.
* **Core Flow**: `User Intent Input` ➔ `AI Query Expansion (Gemini Flash)` ➔ `Telegram MTProto Search (Telethon)` ➔ `Candidate Deduplication & Exclusion (ignoring already added chats)` ➔ `Vector Embeddings & Semantic Scoring (Gemini Embeddings + Flash Reranker)` ➔ `Progressive Streaming Results` ➔ `1-Click Add to Auto-Poster`.
* **Primary Technologies**:
  * **AI Intelligence**: Google Gemini 2.5/Flash (`gemini-2.5-flash` or `gemini-1.5-flash`) for intent expansion, classification, and scoring + `text-embedding-004` for semantic cosine similarity.
  * **Search Engine**: Telethon MTProto Client (`contacts.SearchRequest`, `messages.SearchGlobal`).
  * **Database**: Supabase PostgreSQL (`discovery_searches`, `discovery_queries`, `discovery_results`).
  * **Frontend**: Telegram Desktop Dark Theme styled streaming cards with live batch loading, filtering, and 1-click posting integration.

---

## 2. Architecture & Components

```mermaid
flowchart TD
    User([👤 User]) -->|1. Natural Language Query| UI[💻 Desktop Web UI]
    UI -->|2. POST /api/discovery/search| API[⚡ Discovery Router]
    
    subgraph AI_Engine ["🧠 AI Intelligence (Google Gemini)"]
        API -->|3. Query Understanding & Expansion| GeminiFlash[🤖 Gemini Flash]
        GeminiFlash -->|4. Primary, Related & Promo Queries| QueryQueue[📋 Search Terms]
        API -->|8. Vector Cosine Similarity| GeminiEmbed[📐 text-embedding-004]
        API -->|9. Promo Detection & Reranking| GeminiRerank[⭐ Gemini Flash Reranker]
    end
    
    subgraph TG_Engine ["📡 Telegram Discovery (Telethon)"]
        QueryQueue -->|5. Multi-Strategy Search| Telethon[👤 Telethon MTProto Client]
        Telethon -->|6. Raw Channel & Group Candidates| Filter[🔍 Deduplication & Exclusion]
    end

    DB_Chats[(🗄️ public.chats)] -->|Exclusion List of already added chats| Filter
    Filter -->|7. Unadded Candidates (Top 30-50)| AI_Engine
    GeminiRerank -->|10. Stream Batches (5 by 5)| UI
    UI -->|11. [ + Add to Posting ]| AutoPoster[(🗄️ public.chats Auto-Poster)]
```

---

## 3. Detailed Component Breakdown

### 3.1. AI Query Understanding & Expansion (`core/ai_discovery.py`)
* **Input**: User natural language query (e.g. `OnlyFans Fansly advertising groups` or `меблі для спальні барахолка`).
* **Processing**:
  * Calls Gemini Flash with structured JSON output schema.
  * Generates:
    * `intent`: `advertising_source` | `marketplace` | `community` | `discussion`.
    * `primary_terms`: Core keywords.
    * `related_terms`: Synonyms, slang, abbreviations (`OF`, `promo`, `shoutout`).
    * `search_queries`: 10–15 prioritized Telegram search terms.

### 3.2. Telegram Discovery & Candidate Collection (`core/finder.py`)
* **Execution**:
  * Runs prioritized queries sequentially via Telethon using:
    * `contacts.SearchRequest(q=term, limit=30)` — matches public group/channel usernames and titles.
    * `messages.SearchGlobal(q=term, limit=30)` — matches active messages where commercial ads or discussions are posted.
* **Exclusion & Deduplication**:
  * **CRITICAL**: Loads all active/existing peers from `public.chats` in Supabase.
  * Automatically filters out and skips any chat that is already in the user's auto-poster list or was marked as `hidden`.
  * Deduplicates candidates across search queries by `telegram_peer_id` and `@username`.

### 3.3. Dual-Layer AI Scoring & Qualification Model
Each candidate is evaluated using a weighted multi-metric formula:

$$\text{Final Score} = (\text{Relevance Score} \times 0.50) + (\text{Promo Score} \times 0.30) + (\text{Activity Score} \times 0.20)$$

1. **Relevance Score (0–100)**:
   * **Semantic Embedding Similarity (40%)**: Cosine similarity between query embedding and candidate text embedding via Gemini `text-embedding-004`.
   * **Exact Keyword Presence (30%)**: Matches in title, username, and description.
   * **Gemini Flash Contextual Relevance (30%)**: Semantic validation of topic alignment.
2. **Promo Score (0–100)**:
   * **Positive Signals (+)**: `promo`, `реклама`, `оголошення`, `барахолка`, `shoutout`, `pr`, `купити`, `продати`, `ads`.
   * **Negative Penalties (-)**: `no ads`, `реклама заборонена`, `strictly discussion`, `ban for ads`.
3. **Activity Score (0–100)**:
   * Last message < 2 hours: **100**
   * Last message < 24 hours: **85**
   * Last message < 7 days: **50**
   * Last message > 30 days: **10**

**Score Classification Tiers**:
* `90–100`: 🔥 **Highly Relevant**
* `75–89`: 🟢 **Relevant**
* `60–74`: 🟡 **Potentially Relevant**
* `< 60`: ⚪ **Filtered Out by Default**

### 3.4. Supabase Database Schema Additions (`database/schema.sql`)
1. **`discovery_searches`**:
   * `id`: uuid PK
   * `query`: text
   * `status`: `pending`, `searching`, `completed`, `failed`
   * `total_candidates`: int
   * `total_relevant`: int
   * `created_at`: timestamptz
2. **`discovery_queries`**:
   * `id`: uuid PK
   * `search_id`: uuid FK
   * `query_text`: text
   * `query_type`: text (`primary`, `related`, `promo`)
   * `results_count`: int
3. **`discovery_results`**:
   * `id`: uuid PK
   * `search_id`: uuid FK
   * `telegram_peer`: text UNIQUE
   * `title`: text
   * `chat_type`: text (`group`, `channel`)
   * `members_count`: int
   * `can_post`: boolean
   * `relevance_score`: int
   * `promo_score`: int
   * `activity_score`: int
   * `final_score`: int
   * `ai_reason`: text (concise explanation of why relevant)
   * `status`: text (`new`, `added_to_posting`, `hidden`)
   * `created_at`: timestamptz

---

## 4. UI/UX & Interaction Design
* **Progressive Streaming View**:
  * Results appear in animated batches (5 by 5) as they are scored.
  * Live status indicator: `AI аналіз запиту ➔ Пошук у Telegram ➔ Векторна оцінка ➔ Готово`.
* **Chat Candidate Card**:
  * Title, username (`@group_name`), Type badge (`👥 Група` / `📢 Канал`).
  * Subscriber / Member count.
  * Permission indicator (`🟢 Можна писати` / `🔒 Тільки читання`).
  * Score Badges: `🔥 96% Final`, `🎯 Promo: 94%`, `⚡ Activity: High`.
  * AI Explanation tooltip: *"Чому релевантно: відкрита група для реклами з регулярними платними промо"*.
  * Action buttons:
    * `[ + Додати в розсилку ]` with interval selector (1h, 24h, 3d).
    * `[ 🔗 Відкрити в Telegram ]`.
    * `[ 👎 Не релевантно ]` (hides and prevents reappearance).
* **Batch Actions**: Checkbox selection for multiple chats with `[ 📥 Додати всі вибрані групи в розсилку ]`.
* **Quick Filters**: Filter by `Всі` / `Тільки Групи (де можна писати)` / `Тільки Канали`, Sort by `Final Score` / `Учасники` / `Активність`.

---

## 5. Security, Rate Limits & Anti-Scraping Compliance
* Search only executes on explicit user trigger (no 24/7 autonomous crawlers).
* Strict query rate limiter with jitter and backoff between Telegram MTProto calls.
* Excludes all existing chats to avoid duplicate network load.
* Full fallback to rule-based keyword scoring if Gemini API experiences network timeouts.
