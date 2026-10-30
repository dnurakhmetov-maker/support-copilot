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

LANG_NAMES = {"ru": "Russian", "kk": "Kazakh (proper Kazakh Cyrillic: ә ғ қ ң ө ұ ү һ і; natural phrasing: «Сәлеметсіз бе», «Құрметті ойыншы», «шағымыңыз қаралды», «чатты 30 минутқа бұғаттау», «көмекке асығамын», «Жабылды.»)",
              "uz": "Uzbek (same script as the player: Latin or Cyrillic)", "en": "English",
              "pt": "Portuguese", "es": "Spanish", "uk": "Russian (player wrote Ukrainian)", "und": "Russian (or the player's language if clearly identifiable)"}


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
draft the reply an administrator would send. The draft is compared side by side with real admin replies, so it must
read like one: same structure, tone and typical decision as the SIMILAR PAST TICKETS.

Facts about the data:
- Complaints come from the launcher form with screenshots attached (see screenshots_attached). The admin sees them,
  you don't. Short texts like "оск", "оск родни", "дм", "osk" are NORMAL complaints, not invalid ones.
  NEVER claim that evidence is missing because the text is short. Ask for video only when the similar cases show
  admins asking for video in that situation (e.g. cheats, DM disputes, deception).
- The reporter's name is unknown. accused_player_nick is the ACCUSED player — never address the reply to that nick.
  Address the player as "уважаемый игрок" (or equivalent) or without a name.
- A video link (<url>, video_link_present=true) is the strongest evidence admins get: complaints with a video usually
  get approved, so do not answer "not enough evidence" to them.
- Reports are short in-game messages. "помогите", "сос", "help", "yordam", "ау" = request for help -> reply like admins do
  ("Приветствую, спешу к вам на помощь" / "слежу за вами"). A number + "дм"/"оск" (e.g. "345 дм") = complaint about
  player with that ID -> "Приветствую, слежу за игроком [345]". Don't threaten a mute for such reports.
  Categories for reports: any request for help/rescue/teleport/stuck/"помогите"/"сос"/"help"/"yordam" -> report.help
  (even if it has no details); a player ID or nick + violation -> report.complaint; how-to questions -> report.question.
  report.offtopic ONLY for greetings, single symbols, nonsense, insults to admins.

Decision guidance (proposed decision, the admin confirms it):
- Accusation complaints (insult, DM, cheats, scam, ads...): propose the most likely outcome given SIMILAR PAST TICKETS.
  For insults/DM with screenshots that is usually approval: "Ваша жалоба рассмотрена и одобрена. Игрок <accused nick>
  получит мут на 30 минут за нарушение пункта 2.1 ОП." Use the punishment type and duration typical in similar cases.
  Set needs_human=true.
- Disputes of the player's own punishment / complaints about an admin: most are rejected ("администратор предоставил
  доказательства вашего нарушения, наказание выдано верно"); propose that, needs_human=true.
- Game-mechanics questions: you do NOT know the game's mechanics. Never invent requirements, places, prices or steps.
  Reply generically like admins do ("уточните в разделе помощи / у игроков вашей организации", "мы не консультируем по
  игровому процессу") unless the exact answer appears in SIMILAR PAST TICKETS.
- Not a complaint at all (a question, thanks, chat spam, nothing against a player): complaint.invalid, short reply
  explaining it is not a violation / the form is wrong, needs_human=false.

Account / payment tickets (hacked account, lost property, donation not received): never refuse and never blame the
player; write a neutral hand-off in the player's language, e.g. "Приветствую, ваше обращение передано администрации,
ожидайте ответа." needs_human=true.

Hard rules:
1. Cite ONLY rule ids listed under RULES, in the exact id form given. Never invent rule numbers. If you mention a rule
   in the text, also put its id in "cited".
2. Never promise an unban or a refund/compensation. Punishment of another player may appear only as the proposed
   decision with needs_human=true.
3. needs_human=true for: payments/donations, account hacking/lost access/lost property, ban appeals, complaints about
   admins, threats outside the game, any accusation complaint, or when unsure.
4. needs_human=false only for: invalid/non-complaints, help/off-topic/ID reports, generic game-mechanics questions.
5. Language: write the WHOLE reply in REPLY LANGUAGE, including the closing word (Kazakh "Жабылды.", Uzbek "Yopildi.",
   English "Closed."). Never mix Russian words into a Kazakh/Uzbek/English reply. Uzbek: same script as the player (Latin or Cyrillic). Kazakh: proper
   Kazakh letters. Ukrainian or unclear text -> Russian.
6. Length: reports 1 sentence; complaints 2-4 sentences in the admin template style (greeting, result, decision,
   "Закрыто." or a polite closing). Plain text, no markdown, no emojis. Inside the text use «ёлочки» quotes, never double quotes.
7. is_spam_or_duplicate=true only for pure noise or insults to admins.

Return ONLY a JSON object:
{"lang": "ru|kk|uz|en|und", "category": "<code>", "priority": "p0|p1|p2|p3",
 "is_spam_or_duplicate": bool,
 "extracted": {"nickname": "<accused nick or null>", "rule_refs": [ids from RULES the player referenced],
               "has_evidence": bool, "server": str|null},
 "draft_text": "<reply>", "cited": [ids used in draft_text], "needs_human": bool,
 "confidence": 0..1, "reasons": ["short reason, ALWAYS in Russian", ...]}"""


def _short(s: str, n: int) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


def build_user_prompt(kind: str, messages: list[dict], meta: dict, rules: list[RulePoint], cases: list[Case],
                      reply_lang: str, style_examples: list[Case] | None = None) -> str:
    conv = "\n".join(f"[{m['role']}] {_short(m['text'], 800)}" + (f" (attachments: {m.get('attachments')})" if m.get("attachments") else "")
                     for m in messages)
    rules_txt = "\n".join(f"- {r.cid} ({r.section_title}): {_short(r.text, 260)}" for r in rules) or "- (none)"
    cases_txt = "\n".join(
        f"#{i + 1} player: {_short(c.player, 200)}\n   admin: {_short(c.admin, 320)}" for i, c in enumerate(cases)
    ) or "(none)"
    style_txt = ""
    if style_examples:
        style_txt = "\n\nSTYLE EXAMPLES — real admin replies in the reply language (copy tone and phrasing, not facts):\n" + \
            "\n".join(f"- {_short(c.admin, 300)}" for c in style_examples)
    meta_txt = ", ".join(f"{k}={v}" for k, v in meta.items() if v not in (None, "", [])) or "-"
    return f"""{taxonomy_block(kind)}

RULES (the only ids you may cite):
{rules_txt}

SIMILAR PAST TICKETS with real admin replies (style and typical decisions; not facts about this case):
{cases_txt}{style_txt}

TICKET kind={kind}; {meta_txt}
{conv}

REPLY LANGUAGE: {LANG_NAMES.get(reply_lang, 'Russian')}"""
