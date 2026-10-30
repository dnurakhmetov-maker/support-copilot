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
    _KK = set("әғқңөұүһі")
    _UZ_CYR = set("ўқғҳ")
    _UK = set("їєґ")
    _UZ_LAT = re.compile(r"\b(\w+o['ʻ‘’]\w*|\w*g['ʻ‘’]\w+|qanday|nega|men|menga|siz|iltimos|yordam|qil\w*|bo['ʻ‘’]?l\w*|kerak|rahmat|salom|akkaunt\w*)\b", re.I)
    _EN = re.compile(r"\b(the|is|are|please|help|my|i|you|how|what|account|stuck|player)\b", re.I)

    def _normalize(text: str, region: str | None = None, server_lang: str | None = None) -> str:
        return text

    def _detect_lang(text: str) -> str:
        low = text.lower()
        cyr = sum("а" <= ch <= "я" or ch in "ёәғқңөұүһіўҳїєґ" for ch in low)
        lat = sum("a" <= ch <= "z" for ch in low)
        if cyr == 0 and lat == 0:
            return "und"
        if cyr >= lat:
            if any(ch in _KK for ch in low) and not any(ch in _UK for ch in low):
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
    meta_fields = {"reported_nick": nickname, "player_rule_field": req.rule_refs_raw, "server_lang": req.server_lang,
                   "video_link_present": has_evidence}
    user = build_user_prompt(req.kind, msgs, meta_fields, rules, cases[:4], lang)

    try:
        res = complete_json(SYSTEM, user)
        meta.llm_latency_s, meta.tokens_in, meta.tokens_out, meta.cost_usd = res.latency_s, res.tokens_in, res.tokens_out, res.cost_usd
        out = json.loads(res.text)
        meta.llm_ok = True
    except (LLMError, json.JSONDecodeError, ValueError) as e:
        meta.error = str(e)[:300]
        meta.latency_s = time.time() - t0
        return _fallback(req, lang, "LLM недоступен — требуется администратор", nickname, has_evidence), meta

    # ---- post-processing: never trust the LLM blindly ----
    codes = {c["code"] for c in load_taxonomy()["categories"] if c["kind"] == req.kind}
    category = out.get("category") if out.get("category") in codes else f"{req.kind}.other" if f"{req.kind}.other" in codes else sorted(codes)[0]
    priority = out.get("priority") if out.get("priority") in PRIORITIES else "p2"
    out_lang = out.get("lang") if out.get("lang") in LANGS else lang
    ex = out.get("extracted") or {}
    player_refs = [cid for cid in (rules_idx.resolve(str(r)) for r in ex.get("rule_refs") or []) if cid]

    draft = None
    reasons = [str(r) for r in (out.get("reasons") or [])][:5]
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

    forced = []
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
