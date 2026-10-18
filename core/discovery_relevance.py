"""Pure query planning and conservative, evidence-based B2B qualification."""

import html
import math
import re
from datetime import datetime, timezone
from typing import Any

PRODUCT_CONTEXT = (
    "B2B buyers of Reddit accounts, custom account preparation and Reddit model promotion: "
    "OnlyFans/Fansly agencies, OFM managers, chatters, adult traffic buyers and webcam studios. "
    "Exclude fan-only adult content, unrelated Reddit communities and general traffic chats."
)
# Tier-1 audiences search in their own language, so the vocabulary is multilingual.
TARGET_LANGUAGES = "English, Spanish, Portuguese, German, French, Italian, Russian and Ukrainian"
SEEDS = [
    "OnlyFans", "OFM", "Fansly", "Reddit OnlyFans", "reddit traffic", "реддит аккаунты",
    "OnlyFans agency", "трафик OnlyFans", "онлифанс агентства", "чаттеры OnlyFans",
    "adult traffic", "брачка", "Fanvue", "ManyVids", "loyalfans", "Webcam студия",
    "CamSoda", "модели 18+", "OF agency", "reddit promotion", "арбитраж adult",
    "OnlyFans SFS", "OF management", "agencia OnlyFans", "tráfico OnlyFans", "agência OnlyFans",
    "OnlyFans Agentur", "agence OnlyFans", "agenzia OnlyFans", "webcam studio",
]
PLATFORMS = re.compile(
    r"\b(?:only\s*fans|onlyfans|fansly|fanvue|many\s*vids|loyal\s*fans|camsoda|ofm|webcam|"
    r"chaturbate|stripchat|myfreecams|fancentro|justforfans|cam\s*models?)\b"
    r"|онли\s*фанс|онлі\s*фанс|вебкам|\badult\b|18\s*\+|\bof\s+(?:models?|agenc\w*|management|creators?)\b", re.I
)
BUSINESS = re.compile(
    r"\b(?:agenc\w*|ofm|management|manager\w*|chatter\w*|traffic|marketing|supplier\w*|"
    r"account\w*|promotion|promoting|monetiz\w*|studio\w*|sfs|shout\s*outs?|promo|subreddits?|"
    r"upvotes?|karma|recruit\w*|vendors?|creators?\s+management|"
    # es / pt / de / fr / it
    r"agencia|agência|agentur|agence|agenzia|gesti[oó]n|gestão|tr[aá]fico|tráfego|trafic|traffico|"
    r"promoci[oó]n|promoção|divulga[cç][aã]o|promozione|cuentas|contas|comptes|konten|werbung|"
    r"reclutamiento|recrutamento|modelos\s+webcam|estudio\s+webcam)\b"
    r"|агентств|агенці|чаттер|чатер|траф[иі]к|арбитраж|арбітраж|аккаунт|акаунт|"
    r"продвиж|просуван|менеджер|студі|студи|монетизац|брачк", re.I
)
BUYER = re.compile(
    r"\b(?:buy|buying|need|looking for|wtb|supplier|busco|compro|procuro|preciso|suche|cherche|cerco)\b"
    r"|куплю|купим|шукаю|ищу|нужны|потрібн", re.I
)
NO_ADS = re.compile(
    r"\bno\s+(?:ads|advertising|promo|spam)\b|реклам[ауыи]\s+(?:запрещ|заборон)|без\s+реклам"
    r"|sin\s+(?:publicidad|anuncios)|sem\s+(?:publicidade|anúncios)|keine\s+werbung|pas\s+de\s+pub", re.I
)
ADS_ALLOWED = re.compile(
    r"\b(?:ads|advertising|promotion|promo)\s+(?:allowed|welcome|ok)\b|post\s+your\s+(?:ads|links|promo)"
    r"|реклам[ау]\s+(?:можно|дозвол)|можна\s+реклам|publicidad\s+permitida|publicidade\s+permitida", re.I
)
REDDIT = re.compile(r"reddit|редд?ит", re.I)
FAN_ONLY = re.compile(r"\b(?:leaks?|nudes?|porn|fan\s*club)\b|слив[ыа]?|порно|фан[ -]?клуб", re.I)
# A promotional message: link, @handle, "DM me", or a price.
PROMO = re.compile(
    r"t\.me/|https?://|@\w{4,}|\b(?:dm|pm)\b|\bwrite\s+me\b|пиши(?:те)?\s+в\s+лс|в\s+лс|[$€₽£]\s?\d|\d\s?(?:usd|eur|\$|€)", re.I
)
TG_LINK = re.compile(r"(?:https?://)?(?:t|telegram)\.me/(?!joinchat/|\+|c/|s/|addstickers|share)([A-Za-z][A-Za-z0-9_]{3,31})", re.I)
TG_MENTION = re.compile(r"(?<![\w/@.])@([A-Za-z][A-Za-z0-9_]{3,31})\b")
# Usernames that are bots or obviously not chats are not worth a ResolveUsername call.
NOT_A_CHAT = re.compile(r"bot$|^(?:telegram|gif|vid|pic|bing|wiki|imdb|like|vote|sticker)$", re.I)

INACTIVE_DAYS = 30
LANGUAGE_WORDS = {
    "en": {"the", "and", "for", "you", "with", "this", "are", "your", "need", "looking"},
    "es": {"el", "los", "las", "para", "con", "que", "una", "por", "del", "busco", "tráfico", "cuentas"},
    "pt": {"os", "as", "para", "com", "que", "uma", "não", "você", "procuro", "tráfego", "contas"},
    "de": {"der", "die", "und", "für", "mit", "ist", "nicht", "ich", "suche", "wir"},
    "fr": {"le", "les", "des", "pour", "avec", "est", "une", "vous", "cherche", "nous"},
    "it": {"il", "gli", "per", "con", "che", "una", "sono", "cerco", "della", "non"},
}


def clean_text(value: str) -> str:
    """Normalize pasted ads/query text without interpreting it as instructions."""
    value = html.unescape(value or "")
    return re.sub(r"\s+", " ", re.sub(r"[​-‏﻿]", "", value)).strip()


def normalize_peer(value: str) -> str:
    value = value.strip().lower().replace("https://t.me/", "").replace("http://t.me/", "")
    return value.removeprefix("t.me/").strip("@/")


def detect_language(texts: list[str]) -> str | None:
    """Cheap language guess for display and filtering; None when there is too little text."""
    text = " ".join(texts)[:6000]
    letters = [ch for ch in text if ch.isalpha()]
    if len(letters) < 20:
        return None
    cyrillic = sum("Ѐ" <= ch <= "ӿ" for ch in letters) / len(letters)
    if cyrillic > 0.3:
        return "uk" if re.search(r"[іїєґ]", text, re.I) and len(re.findall(r"[іїєґ]", text, re.I)) >= 3 else "ru"
    latin = sum(ch.isascii() or "À" <= ch <= "ɏ" for ch in letters) / len(letters)
    if latin < 0.5:
        return "other"
    words = re.findall(r"[a-zà-ÿ]+", text.lower())
    scores = {lang: sum(w in vocab for w in words) for lang, vocab in LANGUAGE_WORDS.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] >= 2 else "en"


def extract_usernames(texts: list[str]) -> list[str]:
    """Public chat usernames referenced by t.me links or @mentions (snowball leads)."""
    found: list[str] = []
    for text in texts:
        for name in [*TG_LINK.findall(text or ""), *TG_MENTION.findall(text or "")]:
            if not NOT_A_CHAT.search(name) and name.lower() not in {f.lower() for f in found}:
                found.append(name)
    return found


def promo_share(messages: list[str]) -> int:
    """Percent of messages that look like advertisements."""
    if not messages:
        return 0
    return round(100 * sum(bool(PROMO.search(m)) for m in messages) / len(messages))


def members_score(count: int | None) -> int:
    if not count or count <= 0:
        return 0
    return min(100, round(math.log10(count) / math.log10(50000) * 100))


def rank(relevance: int, members: int | None, activity: int, ad_policy: str) -> int:
    """Usefulness for advertising: audience fit first, then reach, liveliness and ad tolerance."""
    ads = {"allowed": 90, "likely_allowed": 70, "unknown": 30, "prohibited": 0}.get(ad_policy, 30)
    return round(relevance * 0.5 + members_score(members) * 0.2 + activity * 0.15 + ads * 0.15)


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
    return output[:200]


def _age_seconds(last_message_at: str | None) -> float | None:
    if not last_message_at:
        return None
    return (datetime.now(timezone.utc) - datetime.fromisoformat(last_message_at)).total_seconds()


def activity_score(last_message_at: str | None) -> int:
    age = _age_seconds(last_message_at)
    if age is None:
        return 0
    return 100 if age < 7200 else 85 if age < 86400 else 50 if age < 604800 else 10


def usefulness_gate(decision: str, reason: str, candidate: dict[str, Any]) -> tuple[str, str]:
    """A topical group is a target only if members can write there and it is alive."""
    if decision != "accepted":
        return decision, reason
    if candidate.get("posting_access") == "restricted":
        return "not_postable", "Тематична група, але писати можуть лише адміністратори."
    age = _age_seconds(candidate.get("last_message_at"))
    if age is not None and age > INACTIVE_DAYS * 86400:
        return "inactive", f"Тематична група, але без повідомлень понад {INACTIVE_DAYS} днів."
    return decision, reason


def channel_is_topical(candidate: dict[str, Any]) -> bool:
    """Metadata-only check: is this channel worth expanding into its discussion group and similar channels?

    Looser than group qualification on purpose (Reddit account sellers count): every
    group reached through it is still qualified on its own content.
    """
    metadata = clean_text(f"{candidate.get('title', '')} {candidate.get('description', '')}")
    if FAN_ONLY.search(metadata) and not BUSINESS.search(metadata):
        return False
    return bool((PLATFORMS.search(metadata) or REDDIT.search(metadata)) and BUSINESS.search(metadata))


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
    reddit = bool(REDDIT.search(" ".join([metadata, *professional_messages])))
    has_buyers = any(BUYER.search(m) and (contextual(m) or (contextual(metadata) and BUSINESS.search(m))) for m in messages)
    topic = "reddit_traffic" if themed and reddit else "ofm_business" if themed else "unverified"
    promo = promo_share(messages)
    senders = candidate.get("message_senders") or 0
    policy = ("prohibited" if NO_ADS.search(description) else "allowed" if ADS_ALLOWED.search(description)
              else "likely_allowed" if promo >= 30 and senders >= 3 else "unknown")
    evidence = ([description[:400]] if contextual(description) else []) + [m[:400] for m in matches[:3]]
    score = (92 if reddit and has_buyers else 85 if reddit else 72) if themed else 20
    decision = "accepted" if themed else "rejected"
    reason = "Професійна OFM/18+ аудиторія; є контекст Reddit-просування." if themed and reddit else "Професійна аудиторія OFM/18+ бізнесу." if themed else "Немає достатнього підтвердження професійної OFM/18+ тематики."
    if FAN_ONLY.search(metadata) and not BUSINESS.search(metadata) and not matches:
        decision, score, reason = "rejected", 0, "Фанатська або контентна група без професійного контексту."
    if candidate.get("type") != "group":
        if channel_is_topical(candidate):
            decision, reason = "channel", "Тематичний канал: писати можуть лише адміни (можлива платна реклама)."
            if candidate.get("linked_peer"):
                reason += f" Група обговорення: {candidate['linked_peer']}."
        else:
            decision, reason = "rejected", "Канал: не є цільовою групою для розміщення оголошень."
    elif not messages and contextual(metadata):
        decision, reason = "unverified", "Є тематичні ознаки, але повідомлення недоступні для перевірки."
    elif not messages and not description:
        decision, reason = "unverified", "Недостатньо доступного контенту для перевірки."
    activity = activity_score(candidate.get("last_message_at"))
    decision, reason = usefulness_gate(decision, reason, candidate)
    count = candidate.get("participants_count")
    if min_members > 0 and (count is None or count < min_members):
        decision = "below_minimum" if count is not None else "unverified"
        reason = f"Менше {min_members} учасників." if count is not None else "Кількість учасників недоступна; поріг не підтверджено."
    language = candidate.get("language") or detect_language([title, description, *messages])
    result.update(
        decision=decision, topic=topic, evidence=evidence, ad_policy=policy, language=language,
        evidence_status="verified" if messages else "partial" if description else "unavailable",
        relevance_score=score, final_score=rank(score, count, activity, policy), activity_score=activity,
        promo_score=90 if policy == "allowed" else 70 if policy == "likely_allowed" else 0 if policy == "prohibited" else 30,
        ad_signals=promo if messages else None,
        classification="relevant" if decision == "accepted" else "possibly_relevant" if decision in {"unverified", "channel"} else "irrelevant",
        ai_reason=reason, assessed_by="rules",
    )
    return result
