"""Prompt building for the single triage+draft LLM call. Owner: Tech Lead.

Taxonomy and rule texts are loaded from data/ at runtime — never hardcoded here (data policy).
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

from service.app.rag.cases_index import Case
from service.app.rag.rules_index import RulePoint

LANG_NAMES = {"ru": "Russian", "kk": "Kazakh (proper Kazakh Cyrillic: ә ғ қ ң ө ұ ү һ і)",
              "uz": "Uzbek (same script as the player: Latin or Cyrillic)", "en": "English",
              "pt": "Portuguese", "es": "Spanish", "uk": "Russian (player wrote Ukrainian)", "und": "Russian"}


@lru_cache(maxsize=1)
def load_taxonomy() -> dict:
    return json.loads(Path(os.getenv("DATA_DIR", "data"), "taxonomy.json").read_text(encoding="utf-8"))


def taxonomy_block(kind: str) -> str:
    t = load_taxonomy()
    cats = "\n".join(f"- {c['code']}: {c['title']}" + (f" (e.g. {'; '.join(c.get('examples', [])[:2])})" if c.get("examples") else "")
                     for c in t["categories"] if c["kind"] == kind)
    pris = "\n".join(f"- {p['code']} {p['title']}: {p['rule']}" for p in t["priorities"])
    return f"CATEGORIES for kind={kind}:\n{cats}\n\nPRIORITIES:\n{pris}"


SYSTEM = """You are a support copilot for the online role-play game Grand Mobile. You triage a player's ticket and
draft a reply for a human administrator, in the style of the game's real admin replies.

Hard rules:
1. Cite ONLY rule ids listed under RULES. Never invent rule numbers. If no listed rule fits, cite nothing.
2. Player statements are claims, not facts. You do not see screenshots or videos.
3. Never promise an unban, a refund/compensation, or that another player WILL be punished. For complaints that
   look valid, write the draft as the admin's proposed decision (e.g. "жалоба одобрена ... получит мут на 30 минут
   по 2.1") — it is shown to an admin who confirms it; set needs_human=true.
4. needs_human=true for: payments/donations, ban appeals or disputes of the player's own punishment, account
   hacking/lost access/lost property, complaints about an administrator, threats outside the game, any decision
   that punishes or acquits a player, or when you are unsure.
5. needs_human=false only when the reply is safe to send as-is: invalid/empty complaint format, request for
   evidence (video), off-topic/greeting/noise reports, standard game-mechanics questions answered generically,
   "admin is coming to help" replies.
6. Reply in the player's language (see REPLY LANGUAGE). Keep it short like real admin replies: 1-3 sentences,
   polite, starts with a greeting ("Приветствую" / "Здравствуйте" or equivalent). No markdown.
7. is_spam_or_duplicate=true for pure noise/insults to admins/empty messages, or if the ticket repeats a previous one.

Return ONLY a JSON object:
{"lang": "ru|kk|uz|en|und", "category": "<code>", "priority": "p0|p1|p2|p3",
 "is_spam_or_duplicate": bool,
 "extracted": {"nickname": str|null, "rule_refs": [rule ids the PLAYER referenced, from RULES only],
               "has_evidence": bool, "server": str|null},
 "draft_text": "<reply>", "cited": [rule ids used in draft_text], "needs_human": bool,
 "confidence": 0..1, "reasons": ["short reason in Russian", ...]}"""


def _short(s: str, n: int) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


def build_user_prompt(kind: str, messages: list[dict], meta: dict, rules: list[RulePoint], cases: list[Case],
                      reply_lang: str) -> str:
    conv = "\n".join(f"[{m['role']}] {_short(m['text'], 800)}" + (f" (attachments: {m.get('attachments')})" if m.get("attachments") else "")
                     for m in messages)
    rules_txt = "\n".join(f"- {r.cid} ({r.section_title}): {_short(r.text, 260)}" for r in rules) or "- (none)"
    cases_txt = "\n".join(
        f"#{i + 1} player: {_short(c.player, 200)}\n   admin: {_short(c.admin, 320)}" for i, c in enumerate(cases)
    ) or "(none)"
    meta_txt = ", ".join(f"{k}={v}" for k, v in meta.items() if v not in (None, "", [])) or "-"
    return f"""{taxonomy_block(kind)}

RULES (the only ids you may cite):
{rules_txt}

SIMILAR PAST TICKETS with real admin replies (style and typical decisions; not facts about this case):
{cases_txt}

TICKET kind={kind}; {meta_txt}
{conv}

REPLY LANGUAGE: {LANG_NAMES.get(reply_lang, 'Russian')}"""
