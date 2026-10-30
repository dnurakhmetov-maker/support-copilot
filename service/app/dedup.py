"""Duplicate detection (docs/API.md, "Duplicates"). Owner: Backend.

check(req) -> str | None
    If the same reporter (reporter_hash) sent a similar text within the last 60 minutes, returns the id of that
    earlier ticket (or a short description if it had no id); otherwise None. Every checked ticket is remembered.

Time: req.created_at (ISO 8601, naive = UTC) if present, else the current time.
Similarity: normalized text -> set of character 3-grams. Duplicate if the texts are equal, or
  Jaccard >= JACCARD_MIN (0.4), or the shorter text is (almost) contained in the longer one (overlap >= OVERLAP_MIN
  with at least MIN_NGRAMS 3-grams, so one-word messages only match exactly).
Different non-empty reported_nick -> not a duplicate (same words about another player is a new complaint).

State is an in-process memory (per worker), bounded per reporter; reset() clears it (tests, batch runs).
"""
from __future__ import annotations

import re
import threading
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from contract.schemas import TriageRequest
from service.app.normalize import normalize

WINDOW = timedelta(minutes=60)
JACCARD_MIN = 0.4  # tuned on train: 0.4-0.6 pairs are mostly rephrased repeats, 0.2-0.4 mostly follow-ups
OVERLAP_MIN = 0.85
MIN_NGRAMS = 6
N = 3
MAX_PER_REPORTER = 50
MAX_REPORTERS = 100_000  # oldest reporters are forgotten first

_NOISE = re.compile(r"<url>|https?://\S+|[^\w\s]|_", re.U)


@dataclass(frozen=True)
class _Seen:
    at: datetime
    ticket_id: str | None
    nick: str | None
    text: str
    grams: frozenset[str]


_lock = threading.Lock()
_seen: dict[str, deque[_Seen]] = defaultdict(lambda: deque(maxlen=MAX_PER_REPORTER))


def reset() -> None:
    with _lock:
        _seen.clear()


def parse_time(value: str | None) -> datetime:
    if value:
        try:
            dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00").replace(" ", "T", 1))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def canon(text: str, region: str | None = None, server_lang: str | None = None) -> str:
    t = normalize(text, region, server_lang).lower().replace("ё", "е")
    return " ".join(_NOISE.sub(" ", t).split())


def ngrams(text: str, n: int = N) -> frozenset[str]:
    padded = f" {text} "
    return frozenset(padded[i:i + n] for i in range(max(1, len(padded) - n + 1)))


def similar(a: _Seen | str, b: _Seen | str) -> bool:
    ta, ga = (a.text, a.grams) if isinstance(a, _Seen) else (a, ngrams(a))
    tb, gb = (b.text, b.grams) if isinstance(b, _Seen) else (b, ngrams(b))
    if not ta or not tb:
        return False
    if ta == tb:
        return True
    inter = len(ga & gb)
    if inter / len(ga | gb) >= JACCARD_MIN:
        return True
    small = min(len(ga), len(gb))
    return small >= MIN_NGRAMS and inter / small >= OVERLAP_MIN


def _describe(s: _Seen) -> str:
    return s.ticket_id or f"обращение того же игрока от {s.at.strftime('%Y-%m-%d %H:%M')} UTC"


def check(req: TriageRequest) -> str | None:
    if not req.reporter_hash:
        return None
    raw = " ".join(m.text for m in req.messages if m.role == "player")
    text = canon(raw, req.region, req.server_lang)
    cur = _Seen(parse_time(req.created_at), req.ticket_id, (req.reported_nick or "").strip().lower() or None,
                text, ngrams(text) if text else frozenset())
    with _lock:
        if req.reporter_hash not in _seen and len(_seen) >= MAX_REPORTERS:
            del _seen[next(iter(_seen))]
        history = _seen[req.reporter_hash]
        found = None
        for prev in history:  # oldest first -> the original ticket of a repeat series wins
            if req.ticket_id and prev.ticket_id == req.ticket_id:
                continue  # the same ticket re-processed is not its own duplicate
            if not timedelta(0) <= cur.at - prev.at <= WINDOW:
                continue
            if cur.nick and prev.nick and cur.nick != prev.nick:
                continue
            if similar(cur, prev):
                found = _describe(prev)
                break
        if not (req.ticket_id and any(p.ticket_id == req.ticket_id for p in history)):
            history.append(cur)
    return found
