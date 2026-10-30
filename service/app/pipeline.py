"""Triage pipeline entry point. Owner: Tech Lead.

normalize/lang -> retrieve (similar cases + candidate rules) -> ONE LLM call -> validate -> guards -> response.
If the LLM fails, the service degrades to a schema-valid "needs human" answer (never a 500).
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field

from contract.schemas import DraftReply, Extracted, TriageRequest, TriageResponse
from service.app.llm.prompt import SYSTEM, build_user_prompt, load_taxonomy
from service.app.llm.provider import LLMError, complete_json
from service.app.rag.cases_index import load_cases
from service.app.rag.rules_index import load_rules
from service.app.validate import validate_draft

LANGS = {"ru", "kk", "uz", "en", "und"}
PRIORITIES = {"p0", "p1", "p2", "p3"}

# --- Backend-owned modules: use them when merged, otherwise a minimal TEMPORARY fallback -------------
try:  # pragma: no cover
    from service.app.normalize import detect_lang as _detect_lang, normalize as _normalize  # type: ignore
except Exception:  # TEMP fallback until Backend's normalize.py lands
    _KK = set("әғқңөұүһ")  # Kazakh-specific; "і" alone is also Ukrainian
    _UZ_CYR = set("ўқғҳ")
    _UK = set("їєґ")
    _UZ_LAT = re.compile(r"\b(\w+o['ʻ‘’]\w*|\w*g['ʻ‘’]\w+|qanday|nega|men|menga|siz|iltimos|yordam|qil\w*|bo['ʻ‘’]?l\w*|kerak|rahmat|salom|akkaunt\w*)\b", re.I)
    _EN = re.compile(r"\b(the|is|are|please|help|my|i|you|how|what|account|stuck|player)\b", re.I)

    _LOOKALIKE = str.maketrans({"ќ": "қ", "Ќ": "Қ", "ґ": "ғ", "Ґ": "Ғ", "ѕ": "ң", "Ѕ": "Ң", "є": "ә", "Є": "Ә",
                                "ј": "ө", "Ј": "Ө", "ў": "ұ", "Ў": "Ұ", "ї": "ү", "Ї": "Ү"})

    def _normalize(text: str, region: str | None = None, server_lang: str | None = None) -> str:
        # cp1251 look-alikes only on Kazakh servers — elsewhere є/ї are real Ukrainian letters (see docs/EDA.md)
        if region == "kz" or server_lang == "kk":
            return text.translate(_LOOKALIKE)
        return text

    def _detect_lang(text: str) -> str:
        low = text.lower()
        cyr = sum("а" <= ch <= "я" or ch in "ёәғқңөұүһіўҳїєґ" for ch in low)
        lat = sum("a" <= ch <= "z" for ch in low)
        if cyr == 0 and lat == 0:
            return "und"
        if cyr >= lat:
            kk_hits = sum(ch in _KK for ch in low)
            if kk_hits >= 2 and not any(ch in _UK for ch in low):
                return "kk"
            if any(ch in _UZ_CYR for ch in low):
                return "uz"
            return "ru"
        if _UZ_LAT.search(low):
            return "uz"
        if _EN.search(low):
            return "en"
        return "und"

try:  # pragma: no cover
    from service.app.guards import check as _guards_check  # type: ignore
except Exception:  # TEMP fallback until Backend's guards.py lands
    _G = {
        "платёж/донат": re.compile(r"(донат|оплат|плат[её]ж|деньги не при|не пришл|гк\b|to'lov|pul)", re.I),
        "взлом/доступ к аккаунту": re.compile(r"(взлом|взлома|украли акк|сменили пароль|нет доступа|не могу войти|hack)", re.I),
        "апелляция наказания": re.compile(r"(не согласен|разбан|пересмотр|обжал|снимите (бан|мут|варн)|выдали? (бан|мут|варн) (без|ни за))", re.I),
    }

    def _guards_check(req: TriageRequest, category: str) -> list[str]:
        text = " ".join(m.text for m in req.messages if m.role == "player")
        return [f"обязательно человеку: {name}" for name, rx in _G.items() if rx.search(text)]
# ---------------------------------------------------------------------------------------------------------


@dataclass
class TriageMeta:
    latency_s: float = 0.0
    llm_latency_s: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    llm_ok: bool = False
    error: str | None = None
    dropped_citations: list[str] = field(default_factory=list)
    invalid_mentions: list[str] = field(default_factory=list)


_STR = {"type": "STRING"}
_STR_N = {"type": "STRING", "nullable": True}
_ARR = {"type": "ARRAY", "items": {"type": "STRING"}}
TRIAGE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "lang": {"type": "STRING", "enum": ["ru", "kk", "uz", "en", "und"]},
        "category": _STR,
        "priority": {"type": "STRING", "enum": ["p0", "p1", "p2", "p3"]},
        "is_spam_or_duplicate": {"type": "BOOLEAN"},
        "extracted": {"type": "OBJECT", "properties": {"nickname": _STR_N, "rule_refs": _ARR,
                                                       "has_evidence": {"type": "BOOLEAN"}, "server": _STR_N}},
        "draft_text": _STR, "cited": _ARR, "needs_human": {"type": "BOOLEAN"},
        "confidence": {"type": "NUMBER"}, "reasons": _ARR,
    },
    "required": ["lang", "category", "priority", "is_spam_or_duplicate", "extracted", "draft_text", "cited",
                 "needs_human", "confidence", "reasons"],
    "propertyOrdering": ["lang", "category", "priority", "is_spam_or_duplicate", "extracted", "draft_text", "cited",
                         "needs_human", "confidence", "reasons"],
}
LATENCY_BUDGET_S = 4.6  # p95 must stay under 5s

_BAD_ESCAPE = re.compile(r'\\(?!["\\/bfnrt]|u[0-9a-fA-F]{4})')


def _parse_llm_json(text: str) -> dict:
    """LLM JSON is sometimes slightly broken (bad \\u escapes, code fences). Repair instead of failing the ticket."""
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`").split("\n", 1)[-1]
    start, end = t.find("{"), t.rfind("}")
    if start >= 0 and end > start:
        t = t[start: end + 1]
    try:
        return json.loads(t, strict=False)
    except json.JSONDecodeError:
        return json.loads(_BAD_ESCAPE.sub(r"\\\\", t), strict=False)


# Complaint categories that are real accusations: the decision depends on evidence only admins see.
AUTO_OK_COMPLAINT = {"complaint.invalid"}
# Priority ceilings from taxonomy criteria (the LLM over-escalates); p0 is reserved for these categories.
P0_ALLOWED = {"report.account", "report.payment"}
# Allowed priority range per category, derived from taxonomy priority criteria. First item = default.
PRIORITY_RANGE = {
    "complaint.insult": ("p2", "p1"), "complaint.dm": ("p2", "p1"), "complaint.other": ("p2", "p1", "p3"),
    "complaint.cheat": ("p1",), "complaint.scam": ("p1", "p2"), "complaint.rmt_ads": ("p1",),
    "complaint.admin": ("p1", "p2"), "complaint.invalid": ("p3",),
    "report.question": ("p3", "p2"), "report.help": ("p3", "p2"), "report.complaint": ("p2", "p1"),
    "report.account": ("p0",), "report.payment": ("p0",), "report.offtopic": ("p3",),
}


def _player_text(req: TriageRequest) -> str:
    return " ".join(m.text for m in req.messages if m.role == "player").strip()


def _candidate_rules(text: str, kind: str, cases, rules_idx, limit: int = 10):
    """Rules the LLM may cite: player-referenced, cited in similar admin replies, and taxonomy rules for the kind."""
    score: dict[str, float] = {}
    for cid in rules_idx.find_all(text):
        score[cid] = score.get(cid, 0) + 3
    for c in cases:
        for cid in rules_idx.find_all(c.admin):
            score[cid] = score.get(cid, 0) + 1 + c.score
    for cat in load_taxonomy()["categories"]:
        if cat["kind"] == kind:
            for raw in cat.get("rules", []):
                cid = raw if raw in rules_idx else rules_idx.resolve(raw)
                if cid:
                    score[cid] = score.get(cid, 0) + 0.5
    ranked = sorted(score, key=lambda k: -score[k])[:limit]
    return [rules_idx.get(cid) for cid in ranked]


def _fallback(req: TriageRequest, lang: str, reason: str, nickname: str | None, has_evidence: bool) -> TriageResponse:
    return TriageResponse(
        lang=lang if lang in LANGS else "und",
        category="complaint.other" if req.kind == "complaint" else "report.question",
        priority="p2", is_spam_or_duplicate=False,
        extracted=Extracted(nickname=nickname, has_evidence=has_evidence),
        draft_reply=None, needs_human=True, confidence=0.0, reasons=[reason])


def triage_with_meta(req: TriageRequest) -> tuple[TriageResponse, TriageMeta]:
    t0 = time.time()
    meta = TriageMeta()
    rules_idx = load_rules()
    raw_text = _player_text(req)
    text = _normalize(raw_text, req.region, req.server_lang) if raw_text else raw_text
    lang = _detect_lang(text) if text else "und"
    has_evidence = bool(re.search(r"<url>|https?://|youtu|vk\.com|disk\.|imgur|rutube", text, re.I))
    nickname = req.reported_nick

    cases = load_cases().search(text or ".", kind=req.kind, k=6, exclude_id=req.ticket_id)
    rules = _candidate_rules(text, req.kind, cases, rules_idx)
    msgs = [{"role": m.role, "text": m.text, "attachments": m.attachments} for m in req.messages if m.role != "admin"]
    shots = req.images_count if req.images_count is not None else sum(m.attachments for m in req.messages)
    meta_fields = {"accused_player_nick": nickname, "player_rule_field": req.rule_refs_raw, "server_lang": req.server_lang,
                   "region": req.region, "screenshots_attached": shots, "video_link_present": has_evidence}
    user = build_user_prompt(req.kind, msgs, meta_fields, rules, cases[:4], lang)

    out = None
    for attempt in range(2):  # one retry if the first call fails fast enough to fit the latency budget
        left = LATENCY_BUDGET_S - (time.time() - t0)
        if left < 1.2:
            break
        try:
            res = complete_json(SYSTEM, user, schema=TRIAGE_SCHEMA, timeout=min(left, 4.0))
            meta.llm_latency_s += res.latency_s
            meta.tokens_in += res.tokens_in
            meta.tokens_out += res.tokens_out
            meta.cost_usd += res.cost_usd
            out = _parse_llm_json(res.text)
            meta.llm_ok, meta.error = True, None
            break
        except (LLMError, json.JSONDecodeError, ValueError) as e:
            meta.error = f"attempt {attempt + 1}: {str(e)[:280]}"
            if "blocked by provider" in str(e) or "disabled" in str(e) or "not set" in str(e):
                break  # retrying won't help
    if out is None:
        meta.latency_s = time.time() - t0
        reason = ("модель отказалась обрабатывать текст (фильтр безопасности провайдера) — требуется администратор"
                  if meta.error and "blocked" in meta.error else "LLM недоступен — требуется администратор")
        return _fallback(req, lang, reason, nickname, has_evidence), meta

    # ---- post-processing: never trust the LLM blindly ----
    codes = {c["code"] for c in load_taxonomy()["categories"] if c["kind"] == req.kind}
    category = out.get("category") if out.get("category") in codes else f"{req.kind}.other" if f"{req.kind}.other" in codes else sorted(codes)[0]
    priority = out.get("priority") if out.get("priority") in PRIORITIES else "p2"
    out_lang = out.get("lang") if out.get("lang") in LANGS else lang
    ex = out.get("extracted") or {}
    player_refs = [cid for cid in (rules_idx.resolve(str(r)) for r in ex.get("rule_refs") or []) if cid]

    draft = None
    # reasons are shown to Russian-speaking admins: keep Cyrillic ones only
    reasons = [str(r) for r in (out.get("reasons") or []) if re.search(r"[А-Яа-яЁё]", str(r))][:5]
    needs_human = bool(out.get("needs_human", True))
    draft_text = (out.get("draft_text") or "").strip()
    if draft_text:
        v = validate_draft(draft_text, [str(c) for c in out.get("cited") or []], rules_idx)
        meta.dropped_citations, meta.invalid_mentions = v.dropped_citations, v.invalid_mentions
        if v.text:
            draft = DraftReply(lang=out_lang, text=v.text, cited=v.cited)
        if v.force_human_reasons:
            needs_human = True
            reasons += v.force_human_reasons

    threat = re.search(r"(угрож\w* (в реал|irl|найти|убить)|найду тебя|вычислю|swat|суицид)", text, re.I)
    allowed = PRIORITY_RANGE.get(category)
    if threat:
        priority = "p0"
    elif allowed and priority not in allowed:
        priority = allowed[0]

    forced = []
    if req.kind == "complaint" and category not in AUTO_OK_COMPLAINT and not out.get("is_spam_or_duplicate"):
        forced.append("решение по жалобе принимает администратор: доказательства (скриншоты/видео) проверяет человек")
    if category in set(load_taxonomy().get("needs_human_always", [])):
        forced.append(f"категория {category} всегда передаётся администратору")
    forced += _guards_check(req, category)
    if forced:
        needs_human = True
        reasons += forced
    if draft is None:
        needs_human = True

    try:
        conf = max(0.0, min(1.0, float(out.get("confidence", 0.5))))
    except (TypeError, ValueError):
        conf = 0.5

    resp = TriageResponse(
        lang=out_lang, category=category, priority=priority,
        is_spam_or_duplicate=bool(out.get("is_spam_or_duplicate", False)),
        extracted=Extracted(nickname=ex.get("nickname") or nickname, rule_refs=player_refs,
                            has_evidence=bool(ex.get("has_evidence", has_evidence)), server=ex.get("server")),
        draft_reply=draft, needs_human=needs_human, confidence=conf, reasons=list(dict.fromkeys(reasons)))
    meta.latency_s = time.time() - t0
    return resp, meta


def triage(req: TriageRequest) -> TriageResponse:
    return triage_with_meta(req)[0]
