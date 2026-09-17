"""Pure query planning and conservative, evidence-based B2B qualification."""

import html
import re
from datetime import datetime, timezone
from typing import Any

PRODUCT_CONTEXT = (
    "B2B buyers of Reddit accounts, custom account preparation and Reddit model promotion: "
    "OnlyFans/Fansly agencies, OFM managers, chatters, adult traffic buyers and webcam studios. "
    "Exclude fan-only adult content, unrelated Reddit communities and general traffic chats."
)
SEEDS = [
    "OnlyFans", "OFM", "Fansly", "Reddit OnlyFans", "reddit traffic", "реддит аккаунты",
    "OnlyFans agency", "трафик OnlyFans", "онлифанс агентства", "чаттеры OnlyFans",
    "adult traffic", "брачка", "Fanvue", "ManyVids", "loyalfans", "Webcam студия",
    "CamSoda", "модели 18+", "OF agency", "reddit promotion", "арбитраж adult",
]
PLATFORMS = re.compile(
    r"\b(?:only\s*fans|fansly|fanvue|many\s*vids|loyal\s*fans|camsoda|ofm|webcam)\b"
    r"|онли\s*фанс|онлі\s*фанс|вебкам|\badult\b|18\s*\+", re.I
)
BUSINESS = re.compile(
    r"\b(?:agenc\w*|ofm|management|manager\w*|chatter\w*|traffic|marketing|supplier\w*|"
    r"account\w*|promotion|promoting|monetiz\w*|studio\w*)\b"
    r"|агентств|агенці|чаттер|чатер|траф[иі]к|арбитраж|арбітраж|аккаунт|акаунт|"
    r"продвиж|просуван|менеджер|студі|студи|монетизац|брачк", re.I
)
BUYER = re.compile(r"\b(?:buy|buying|need|looking for|wtb|supplier)\b|куплю|купим|шукаю|ищу|нужны|потрібн", re.I)
NO_ADS = re.compile(r"\bno\s+(?:ads|advertising|promo|spam)\b|реклам[ауыи]\s+(?:запрещ|заборон)|без\s+реклам", re.I)
ADS_ALLOWED = re.compile(r"\b(?:ads|advertising|promotion)\s+(?:allowed|welcome)\b|реклам[ау]\s+(?:можно|дозвол)|можна\s+реклам", re.I)
FAN_ONLY = re.compile(r"\b(?:leaks?|nudes?|porn|fan\s*club)\b|слив[ыа]?|порно|фан[ -]?клуб", re.I)


def clean_text(value: str) -> str:
    """Normalize pasted ads/query text without interpreting it as instructions."""
    value = html.unescape(value or "")
    return re.sub(r"\s+", " ", re.sub(r"[\u200b-\u200f\ufeff]", "", value)).strip()


def normalize_peer(value: str) -> str:
    value = value.strip().lower().replace("https://t.me/", "").replace("http://t.me/", "")
    return value.removeprefix("t.me/").strip("@/")


def plan_queries(user_query: str, additions: list[str] | None = None) -> list[str]:
    parts = re.split(r"[,;\n|]+", user_query)
    # A long comma-free list should not become a single impossible directory query.
    if len(parts) == 1 and len(parts[0].split()) > 6:
        parts = re.findall(r"OnlyFans|Fansly|Reddit|Adult|Fanvue|ManyVids|Webcam|OFM|CamSoda|loyalfans|брачка|трафик", user_query, re.I)
    output: list[str] = []
    for raw in [*parts, *SEEDS, *(additions or [])]:
        if not isinstance(raw, str):
            continue
        term = clean_text(raw)[:80]
        if term.lower() in {"of", "fans", "traffic", "трафик", "трафік", "reddit"}:
            term = {"of": "OF agency", "fans": "OnlyFans agency", "reddit": "Reddit OnlyFans"}.get(term.lower(), "adult traffic")
        if len(term) >= 3 and term.lower() not in {q.lower() for q in output}:
            output.append(term)
    return output[:120]


def qualify(candidate: dict[str, Any], min_members: int = 0) -> dict[str, Any]:
    """Require business evidence; a keyword match alone is never a qualified lead."""
    result = dict(candidate)
    description = clean_text(candidate.get("description", ""))
    messages = [clean_text(m) for m in candidate.get("messages", []) if isinstance(m, str) and clean_text(m)]
    title = clean_text(candidate.get("title", ""))
    metadata = f"{title} {description}"
    contextual = lambda text: bool(PLATFORMS.search(text) and BUSINESS.search(text))
    matches = list(dict.fromkeys(m for m in messages if contextual(m)))
    professional_messages = list(dict.fromkeys(m for m in messages if BUSINESS.search(m)))
    # One stray advertisement in an unrelated group is not evidence of its topic.
    themed = (contextual(metadata) and bool(professional_messages)) or (len(matches) >= 2 and len(matches) / max(1, len(messages)) >= 0.25)
    reddit = bool(re.search(r"reddit|редд?ит", " ".join([metadata, *professional_messages]), re.I))
    has_buyers = any(BUYER.search(m) and (contextual(m) or (contextual(metadata) and BUSINESS.search(m))) for m in messages)
    topic = "reddit_traffic" if themed and reddit else "ofm_business" if themed else "unverified"
    policy = "prohibited" if NO_ADS.search(description) else "allowed" if ADS_ALLOWED.search(description) else "unknown"
    evidence = ([description[:400]] if contextual(description) else []) + [m[:400] for m in matches[:3]]
    score = (92 if reddit and has_buyers else 85 if reddit else 72) if themed else 20
    decision = "accepted" if themed else "rejected"
    reason = "Професійна OFM/18+ аудиторія; є контекст Reddit-просування." if themed and reddit else "Професійна аудиторія OFM/18+ бізнесу." if themed else "Немає достатнього підтвердження професійної OFM/18+ тематики."
    if FAN_ONLY.search(metadata) and not BUSINESS.search(metadata) and not matches:
        decision, score, reason = "rejected", 0, "Фанатська або контентна група без професійного контексту."
    if candidate.get("type") != "group":
        decision, reason = "rejected", "Канал: не є цільовою групою для розміщення оголошень."
    elif not messages and contextual(metadata):
        decision, reason = "unverified", "Є тематичні ознаки, але повідомлення недоступні для перевірки."
    elif not messages and not description:
        decision, reason = "unverified", "Недостатньо доступного контенту для перевірки."
    count = candidate.get("participants_count")
    if min_members > 0 and (count is None or count < min_members):
        decision = "below_minimum" if count is not None else "unverified"
        reason = f"Менше {min_members} учасників." if count is not None else "Кількість учасників недоступна; поріг не підтверджено."
    activity = 0
    if candidate.get("last_message_at"):
        last = datetime.fromisoformat(candidate["last_message_at"])
        age = (datetime.now(timezone.utc) - last).total_seconds()
        activity = 100 if age < 7200 else 85 if age < 86400 else 50 if age < 604800 else 10
    result.update(
        decision=decision, topic=topic, evidence=evidence, ad_policy=policy,
        evidence_status="verified" if messages else "partial" if description else "unavailable",
        relevance_score=score, final_score=score, activity_score=activity,
        promo_score=90 if policy == "allowed" else 0 if policy == "prohibited" else 30,
        classification="relevant" if decision == "accepted" else "possibly_relevant" if decision == "unverified" else "irrelevant",
        ai_reason=reason, assessed_by="rules",
    )
    return result
