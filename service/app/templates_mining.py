"""Bonus: propose new reply templates from clusters of similar tickets. Owner: Tech Lead.

  python -m service.app.templates_mining --k 30

Clusters player texts (char n-gram TF-IDF + MiniBatchKMeans) per ticket kind, and for each cluster proposes a reply
template = the most typical admin reply in it (nicknames/ids replaced by placeholders), with size, verdict mix and
how consistent admins already are. Output: data/templates/proposals.json (local only — derived from case data).
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
from pathlib import Path

import numpy as np
from sklearn.cluster import MiniBatchKMeans
from sklearn.feature_extraction.text import TfidfVectorizer

from service.app.rag.cases_index import load_cases

_STOP = ("не на что за меня он она они я ты вы мы и в во с со по к ко от до из у о об а но да же ли бы то это как так "
         "мне его её ее их был была были есть просто тут там вот уже еще ещё всё все когда где url digits handle the a "
         "is to and of in i my me bu men va").split()

_NICK = re.compile(r"\b[A-Z][a-z]+_[A-Z][a-z]+\d*\b")
_ID = re.compile(r"\[\d+\]|#\s*<digits>|<digits>")
_WS = re.compile(r"\s+")


def templatize(admin: str) -> str:
    t = _NICK.sub("{nick}", admin)
    t = _ID.sub("{id}", t)
    return _WS.sub(" ", t).strip()


def _skeleton(t: str) -> str:
    """Comparable form of a reply: lowercase, digits collapsed — to count how often admins reuse it."""
    return re.sub(r"\d+", "0", t.lower())[:160]


def mine(kind: str, k: int, min_size: int = 80) -> list[dict]:
    idx = load_cases()
    sel = [i for i, c in enumerate(idx.cases) if c.kind == kind]
    texts = [idx.cases[i].player for i in sel]
    vec = TfidfVectorizer(analyzer="word", token_pattern=r"(?u)\b\w\w+\b", min_df=5, max_df=0.3, stop_words=_STOP,
                          sublinear_tf=True, lowercase=True)
    X = vec.fit_transform(texts)
    km = MiniBatchKMeans(n_clusters=k, random_state=0, n_init=3, batch_size=4096).fit(X)
    terms = np.array(vec.get_feature_names_out())
    out = []
    for c in range(k):
        members = [sel[j] for j in np.where(km.labels_ == c)[0]]
        if len(members) < min_size:
            continue
        cases = [idx.cases[i] for i in members]
        skel = collections.Counter(_skeleton(templatize(x.admin)) for x in cases)
        top_skel, top_n = skel.most_common(1)[0]
        example = next(templatize(x.admin) for x in cases if _skeleton(templatize(x.admin)) == top_skel)
        verdicts = collections.Counter(x.verdict or "-" for x in cases).most_common(4)
        langs = collections.Counter(x.lang for x in cases).most_common(3)
        out.append({
            "kind": kind, "cluster": c, "size": len(members),
            "top_terms": terms[np.argsort(-km.cluster_centers_[c])[:8]].tolist(),
            "verdicts": {v: round(n / len(cases), 2) for v, n in verdicts},
            "langs": dict(langs),
            "proposed_template": example,
            "template_reuse_share": round(top_n / len(cases), 3),
            "distinct_replies_share": round(len(skel) / len(cases), 3),
            "sample_player_texts": [x.player[:80] for x in cases[:3]],
        })
    # most valuable first: big clusters where admins write many different replies (a template would save time)
    out.sort(key=lambda r: -r["size"] * r["distinct_replies_share"])
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=30)
    a = ap.parse_args()
    res = mine("complaint", a.k) + mine("report", a.k)
    path = Path(os.getenv("DATA_DIR", "data")) / "templates" / "proposals.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(res)} clusters -> {path}")


if __name__ == "__main__":
    main()
