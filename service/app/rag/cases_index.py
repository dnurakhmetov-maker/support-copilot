"""Index of past tickets with admin replies (RAG source #2). Owner: Tech Lead.

Character n-gram TF-IDF over the player's text: robust to typos, slang, translit and mixed languages,
runs locally in milliseconds, costs nothing. Built from data/tickets_train.jsonl.gz, cached to INDEX_DIR.
"""
from __future__ import annotations

import gzip
import json
import os
import pickle
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

DATA_DIR = os.getenv("DATA_DIR", "data")
INDEX_DIR = os.getenv("INDEX_DIR", os.path.join(DATA_DIR, "index"))


def player_text(t: dict) -> str:
    return " ".join(m["text"] for m in t.get("messages", []) if m.get("role") == "player").strip()


def admin_text(t: dict) -> str:
    return " ".join(m["text"] for m in t.get("messages", []) if m.get("role") == "admin").strip()


_UZ_LAT_MARKERS = ("hurmatli", "shikoyat", "rahmat", "yordam", "assalom", "o‘yin", "o'yin", "kuzat", "ma’muriyat")


def _reply_lang(text: str) -> str:
    low = text.lower()
    if sum(ch in "әңөұүһ" for ch in low) >= 2:
        return "kk"
    if any(m in low for m in _UZ_LAT_MARKERS) or sum(ch in "ўҳ" for ch in low) >= 2:
        return "uz"
    return "other"


@dataclass
class Case:
    ticket_id: str
    kind: str
    lang: str
    player: str
    admin: str
    verdict: str | None
    punishment: str | None
    violation: str | None
    score: float = 0.0


class CasesIndex:
    def __init__(self, cases: list[Case]):
        self.cases = cases
        self.kinds = np.array([c.kind for c in cases])
        self.vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), min_df=3, max_features=300_000,
                                   sublinear_tf=True, lowercase=True, dtype=np.float32)
        self.X = self.vec.fit_transform([c.player for c in cases])

    @property
    def admin_langs(self) -> np.ndarray:
        """Language of each ADMIN reply (kk / uz / other) — for style examples in the reply language."""
        if getattr(self, "_admin_langs", None) is None:
            self._admin_langs = np.array([_reply_lang(c.admin) for c in self.cases])
        return self._admin_langs

    def search(self, text: str, kind: str | None = None, k: int = 5, exclude_id: str | None = None,
               admin_lang: str | None = None) -> list[Case]:
        q = self.vec.transform([text])
        sims = (self.X @ q.T).toarray().ravel()
        if kind:
            sims = np.where(self.kinds == kind, sims, -1.0)
        if admin_lang:
            sims = np.where(self.admin_langs == admin_lang, sims, -1.0)
            if not (sims > -1.0).any():
                return []
        top = np.argpartition(-sims, min(k + 5, len(sims) - 1))[: k + 5]
        top = top[np.argsort(-sims[top])]
        out = []
        for i in top:
            c = self.cases[i]
            if exclude_id and c.ticket_id == exclude_id:
                continue
            out.append(Case(**{**c.__dict__, "score": float(sims[i])}))
            if len(out) == k:
                break
        return out


def build(train_path: str | None = None) -> CasesIndex:
    path = train_path or os.path.join(DATA_DIR, "tickets_train.jsonl.gz")
    cases = []
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            t = json.loads(line)
            p, a = player_text(t), admin_text(t)
            if not p or not a:
                continue
            cases.append(Case(t["ticket_id"], t["kind"], t.get("lang") or "und", p, a,
                              t.get("verdict"), t.get("punishment"), t.get("violation")))
    return CasesIndex(cases)


@lru_cache(maxsize=1)
def load_cases() -> CasesIndex:
    cache = Path(INDEX_DIR) / "cases.pkl"
    if cache.exists():
        idx = pickle.loads(cache.read_bytes())
        idx.admin_langs  # warm up (~2s once per process)
        return idx
    idx = build()
    idx.admin_langs
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(pickle.dumps(idx))
    return idx
