import logging
import json
import re
from typing import List, Dict, Any, Optional
import numpy as np
import google.generativeai as genai
from config import config

logger = logging.getLogger("AIDiscovery")

# Configure Gemini
_ai_initialized = False
if config.GEMINI_API_KEY:
    try:
        genai.configure(api_key=config.GEMINI_API_KEY)
        _ai_initialized = True
        logger.info("Google Gemini AI successfully initialized.")
    except Exception as e:
        logger.error(f"Failed to configure Google Gemini AI: {e}")


class AIDiscoveryEngine:
    """Intelligent Query Expansion, Embeddings & Semantic Scoring using Google Gemini with Multi-Model Fallback."""

    # Multi-model cascade priority: 3.5 Flash Lite (Primary) -> 3.5 Flash -> 3.6 Flash -> 3.7 Flash -> 3.1 Pro -> 2.5 Flash -> 1.5 Flash
    GENERATION_MODELS = [
        "gemini-3.5-flash-lite",
        "gemini-3.5-flash",
        "gemini-3.6-flash",
        "gemini-3.7-flash",
        "gemini-3.1-pro-preview",
        "gemini-3.1-pro",
        "gemini-2.5-flash",
        "gemini-1.5-flash",
        "gemini-1.5-pro"
    ]
    EMBEDDING_MODELS = [
        "models/gemini-embedding-001",
        "models/text-embedding-004"
    ]

    @classmethod
    def generate_title_for_post(cls, content: str) -> str:
        """Generate a short, catchy title (2-4 words) for an ad post using AI cascade."""
        if not _ai_initialized or not content.strip():
            return "Оголошення"
        prompt = f"""
Analyze this ad text and generate a short, catchy title (2 to 4 words in Ukrainian or English matching the text language).
Examples: "Оголошення Fansly", "Барахолка Київ", "Крипто обмін P2P", "OnlyFans Agency Promo".

Ad text:
{content[:1000]}

Return ONLY structured JSON:
{{"title": "Short Title"}}
"""
        try:
            data = cls.generate_json_with_fallback(prompt)
            if data and isinstance(data, dict) and data.get("title"):
                return data["title"].strip()
        except Exception as e:
            logger.warning(f"Failed to generate title: {e}")
        return "Оголошення"

    @classmethod
    def generate_json_with_fallback(cls, prompt: str) -> Optional[Any]:
        """Attempt generating structured JSON content through model hierarchy with quota fallback."""
        if not _ai_initialized:
            return None

        last_error = None
        for model_name in cls.GENERATION_MODELS:
            try:
                model = genai.GenerativeModel(model_name)
                response = model.generate_content(
                    prompt,
                    generation_config={"response_mime_type": "application/json"}
                )
                raw_text = response.text.strip()
                raw_text = re.sub(r"^```json\s*", "", raw_text)
                raw_text = re.sub(r"\s*```$", "", raw_text)
                data = json.loads(raw_text)
                logger.info(f"Successfully generated AI response using model: {model_name}")
                return data
            except Exception as e:
                err_str = str(e).lower()
                last_error = e
                if "quota" in err_str or "resourceexhausted" in err_str or "429" in err_str or "limit" in err_str:
                    logger.warning(f"⚠️ Model {model_name} quota/rate limit exceeded. Falling back to next model...")
                else:
                    logger.warning(f"Model {model_name} call failed: {e}. Falling back to next model...")
                continue

        logger.error(f"All Gemini models in fallback cascade failed. Last error: {last_error}")
        return None

    @classmethod
    def expand_query(cls, user_query: str) -> Dict[str, Any]:
        """Analyze intent and generate 10-15 contextual Telegram search queries."""
        default_result = cls._fallback_expansion(user_query)
        if not _ai_initialized:
            return default_result

        prompt = f"""
You are an expert AI assistant specialized in Telegram marketing and group discovery.
Analyze the user's search query and generate high-quality search terms to find relevant Telegram public groups and channels.

User Query: "{user_query}"

Generate structured JSON matching this exact format:
{{
  "intent": "advertising_source", // "advertising_source" | "marketplace" | "community" | "discussion"
  "primary_terms": ["term1", "term2"],
  "related_terms": ["synonym1", "synonym2", "slang", "abbreviation"],
  "search_queries": [
    {{"text": "query string", "type": "primary", "priority": 1}},
    {{"text": "query string", "type": "promo", "priority": 1}},
    {{"text": "query string", "type": "related", "priority": 2}}
  ]
}}

Rules:
1. Provide 10 to 15 distinct, realistic Telegram search queries in Ukrainian, English, or the language of the query.
2. If the user's intent is advertising / promo / sales (e.g. OnlyFans, clothes, furniture, crypto, cars), include search terms with commercial keywords: "promo", "reklama", "ads", "shoutout", "barakholka", "board", "ogoloshennya", "chat".
3. Return ONLY valid raw JSON without markdown codeblocks or extra text.
"""
        data = cls.generate_json_with_fallback(prompt)
        if data and "search_queries" in data and len(data["search_queries"]) > 0:
            logger.info(f"Generated {len(data['search_queries'])} search queries via Gemini multi-model cascade.")
            return data

        return default_result

    @classmethod
    def get_embedding(cls, text: str) -> Optional[List[float]]:
        """Calculate dense text embedding with quota fallback across embedding models."""
        if not _ai_initialized or not text.strip():
            return None

        for model_name in cls.EMBEDDING_MODELS:
            try:
                res = genai.embed_content(
                    model=model_name,
                    content=text[:1000]
                )
                emb = res.get("embedding")
                if emb:
                    return emb
            except Exception as e:
                err_str = str(e).lower()
                if "quota" in err_str or "429" in err_str or "resourceexhausted" in err_str:
                    logger.warning(f"⚠️ Embedding model {model_name} quota exceeded, trying backup model...")
                else:
                    logger.warning(f"Embedding model {model_name} failed: {e}")
                continue
        return None

    @classmethod
    def cosine_similarity(cls, vec_a: Optional[List[float]], vec_b: Optional[List[float]]) -> float:
        """Compute cosine similarity between two embedding vectors."""
        if not vec_a or not vec_b:
            return 0.5
        try:
            a = np.array(vec_a, dtype=float)
            b = np.array(vec_b, dtype=float)
            norm_a = np.linalg.norm(a)
            norm_b = np.linalg.norm(b)
            if norm_a == 0 or norm_b == 0:
                return 0.5
            sim = float(np.dot(a, b) / (norm_a * norm_b))
            return max(0.0, min(1.0, (sim + 1.0) / 2.0))
        except Exception:
            return 0.5

    @classmethod
    def batch_qualify_candidates(
        cls,
        user_query: str,
        query_embedding: Optional[List[float]],
        candidates: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Evaluate semantic relevance, promo suitability, and generate AI explanations."""
        if not candidates:
            return []

        # 1. First pass: Vector similarity & Activity heuristics
        for c in candidates:
            title_desc = f"{c.get('title', '')} {c.get('description', '')}".strip()
            cand_embed = cls.get_embedding(title_desc)
            vec_sim = cls.cosine_similarity(query_embedding, cand_embed) if query_embedding and cand_embed else 0.6
            c["vec_sim"] = vec_sim

            # Keyword presence score
            words = set(re.findall(r"\w+", user_query.lower()))
            content_words = set(re.findall(r"\w+", title_desc.lower()))
            overlap = len(words.intersection(content_words))
            kw_score = min(100, int((overlap / max(1, len(words))) * 100))
            c["kw_score"] = kw_score

            # Activity score based on participants and permissions
            act_score = 75
            part_count = c.get("participants_count", 0)
            if part_count > 5000:
                act_score = 95
            elif part_count > 1000:
                act_score = 85
            elif part_count > 200:
                act_score = 70
            else:
                act_score = 50
            c["activity_score"] = act_score

        # 2. Second pass: Gemini contextual qualification for the top candidates
        if _ai_initialized and candidates:
            try:
                cand_summary = []
                for idx, c in enumerate(candidates[:25]):
                    cand_summary.append({
                        "id": idx,
                        "title": c.get("title", ""),
                        "username": c.get("peer", ""),
                        "type": c.get("type", "group"),
                        "description": (c.get("description") or "")[:200]
                    })

                prompt = f"""
You are an expert Telegram Discovery AI.
User Search Query: "{user_query}"

Evaluate the following candidate Telegram groups/channels for advertising/posting suitability:
{json.dumps(cand_summary, ensure_ascii=False)}

For each candidate item (by id), return:
- relevance_score: integer 0-100 (how relevant to the topic)
- promo_score: integer 0-100 (how suitable for ads/promo/selling; look for words like promo, ads, shop, market, board. Penalize if explicitly "no ads" or personal blog)
- ai_reason: short explanation in Ukrainian (1 sentence, e.g. "Відкрита група для реклами з високою активністю")
- classification: "highly_relevant" | "relevant" | "possibly_relevant" | "irrelevant"

Return a JSON array of objects:
[
  {{"id": 0, "relevance_score": 95, "promo_score": 90, "ai_reason": "...", "classification": "highly_relevant"}}, ...
]
"""
                eval_list = cls.generate_json_with_fallback(prompt)
                if eval_list and isinstance(eval_list, list):
                    eval_map = {item["id"]: item for item in eval_list if isinstance(item, dict) and "id" in item}
                    for idx, c in enumerate(candidates[:25]):
                        if idx in eval_map:
                            ev = eval_map[idx]
                            c["relevance_score"] = int(ev.get("relevance_score", 70))
                            c["promo_score"] = int(ev.get("promo_score", 70))
                            c["ai_reason"] = ev.get("ai_reason", "Тематичний Telegram-чат")
                            c["classification"] = ev.get("classification", "relevant")
            except Exception as eval_err:
                logger.warning(f"Gemini candidate evaluation failed, using rule-based scoring: {eval_err}")

        # 3. Final Score calculation
        for c in candidates:
            if "relevance_score" not in c:
                vec_s = int(c.get("vec_sim", 0.6) * 100)
                kw_s = c.get("kw_score", 60)
                c["relevance_score"] = int(vec_s * 0.5 + kw_s * 0.5)

            if "promo_score" not in c:
                promo_markers = ["реклама", "оголошення", "барахолка", "promo", "shoutout", "купити", "продати", "market", "chat"]
                text_lower = f"{c.get('title', '')} {c.get('description', '')}".lower()
                matched_p = sum(1 for m in promo_markers if m in text_lower)
                c["promo_score"] = min(100, 50 + (matched_p * 15))

            if "ai_reason" not in c:
                c["ai_reason"] = "Знайдено за семантичною схожістю з вашим запитом"

            # Weighted Formula: 50% Relevance + 30% Promo + 20% Activity
            final_s = int(
                (c["relevance_score"] * 0.50) +
                (c["promo_score"] * 0.30) +
                (c["activity_score"] * 0.20)
            )
            c["final_score"] = max(0, min(100, final_s))

            if not c.get("classification"):
                if final_s >= 90:
                    c["classification"] = "highly_relevant"
                elif final_s >= 75:
                    c["classification"] = "relevant"
                elif final_s >= 60:
                    c["classification"] = "possibly_relevant"
                else:
                    c["classification"] = "irrelevant"

        # Sort descending by final score
        candidates.sort(key=lambda x: x.get("final_score", 0), reverse=True)
        return candidates

    @classmethod
    def _fallback_expansion(cls, user_query: str) -> Dict[str, Any]:
        """Local heuristic query expansion when AI is offline."""
        q = user_query.strip()
        queries = [
            {"text": q, "type": "primary", "priority": 1},
            {"text": f"{q} реклама", "type": "promo", "priority": 1},
            {"text": f"{q} оголошення", "type": "promo", "priority": 1},
            {"text": f"{q} барахолка", "type": "promo", "priority": 2},
            {"text": f"{q} чат", "type": "related", "priority": 2},
            {"text": f"{q} promo", "type": "promo", "priority": 2},
            {"text": f"{q} group", "type": "related", "priority": 3}
        ]
        return {
            "intent": "advertising_source",
            "primary_terms": [q],
            "related_terms": ["реклама", "оголошення", "promo", "чат"],
            "search_queries": queries
        }
