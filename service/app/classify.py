"""Local complaint classifier trained on silver labels (real admin outcomes). Owner: Tech Lead.

Why: on admin-grounded silver labels it beats the LLM on complaint categories (≈0.85 vs ≈0.67 when the LLM
answers at all), costs nothing, runs in ms and still works when the LLM times out.

Scope: complaint categories that are about WHAT happened (insult, dm, cheat, scam, rmt_ads, admin). It does NOT
predict complaint.invalid: the admin "invalid format" verdict depends on form details we never see, while the
taxonomy's "not a complaint" is a text judgement left to the LLM. Reports have no silver labels -> LLM only.

  python -m service.app.classify --eval     # train on train-silver, report accuracy on valid-silver
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import pickle
from functools import lru_cache
from pathlib import Path

DATA = Path(os.getenv("DATA_DIR", "data"))
CACHE = Path(os.getenv("INDEX_DIR", str(DATA / "index"))) / "complaint_clf.pkl"
EXCLUDED = {"complaint.invalid"}


def _player(t: dict) -> str:
    return " ".join(m["text"] for m in t.get("messages", []) if m.get("role") == "player").strip()


def _silver_rows(path: Path, gz: bool) -> list[tuple[str, str, str]]:
    from service.app.silver_labels import rule_to_category, silver
    r2c = rule_to_category()
    opener = gzip.open if gz else open
    rows = []
    with opener(path, "rt", encoding="utf-8") as f:
        for line in f:
            t = json.loads(line)
            c = silver(t, r2c)
            if c and c not in EXCLUDED:
                rows.append((_player(t), c, t["ticket_id"]))
    return rows


def train():
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline, make_union
    rows = _silver_rows(DATA / "tickets_train.jsonl.gz", gz=True)
    clf = make_pipeline(
        make_union(TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 5), min_df=3, sublinear_tf=True,
                                   max_features=200_000),
                   TfidfVectorizer(analyzer="word", ngram_range=(1, 2), min_df=2, sublinear_tf=True)),
        LogisticRegression(max_iter=2000, C=4, class_weight="balanced"))
    clf.fit([r[0] for r in rows], [r[1] for r in rows])
    return clf


@lru_cache(maxsize=1)
def load_classifier():
    if CACHE.exists():
        return pickle.loads(CACHE.read_bytes())
    clf = train()
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_bytes(pickle.dumps(clf))
    return clf


def predict(text: str) -> tuple[str, float]:
    """(category, probability) for a complaint text."""
    clf = load_classifier()
    proba = clf.predict_proba([text or "."])[0]
    i = int(proba.argmax())
    return str(clf.classes_[i]), float(proba[i])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--eval", action="store_true")
    a = ap.parse_args()
    clf = load_classifier()
    if a.eval:
        rows = _silver_rows(DATA / "tickets_valid.jsonl", gz=False)
        import numpy as np
        proba = clf.predict_proba([r[0] for r in rows])
        pred = clf.classes_[proba.argmax(1)]
        conf = proba.max(1)
        gold = np.array([r[1] for r in rows])
        print(f"valid silver (excl. invalid): acc {np.mean(pred == gold):.3f} on {len(rows)}")
        for th in (0.5, 0.6, 0.7, 0.8, 0.9):
            m = conf >= th
            print(f"  p>={th}: coverage {m.mean():.0%}, acc {np.mean(pred[m] == gold[m]):.3f}")
