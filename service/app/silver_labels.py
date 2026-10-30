"""Silver category labels for complaints, derived from the REAL admin outcome. Owner: Tech Lead.

The case gives no category labels, but each complaint carries what the admin actually decided: `verdict` and the
violated rule (`violation`, e.g. "2.1 ОП"). Mapping rule -> taxonomy category (taxonomy lists rules per category)
gives an objective, admin-grounded label for a subset of complaints. It complements the hand-checked gold set.

  python -m service.app.silver_labels                    # build data/labels/silver_valid.jsonl, print coverage
  python -m service.app.silver_labels --score RUN.jsonl  # category accuracy of a smoke/predict run vs silver
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
from pathlib import Path

from service.app.llm.prompt import load_taxonomy
from service.app.rag.rules_index import load_rules

DATA = Path(os.getenv("DATA_DIR", "data"))
_SLANG = {"dm": "complaint.dm", "дм": "complaint.dm", "db": "complaint.dm", "sk": "complaint.dm", "tk": "complaint.dm",
          "чит": "complaint.cheat", "софт": "complaint.cheat"}


def rule_to_category() -> dict[str, str]:
    idx = load_rules()
    m: dict[str, str] = {}
    for cat in load_taxonomy()["categories"]:
        if cat["kind"] != "complaint":
            continue
        for raw in cat.get("rules", []):
            cid = raw if raw in idx else idx.resolve(raw)
            if cid:
                m.setdefault(cid, cat["code"])
            elif raw.upper() in ("GZ",):  # whole green-zone section
                for p in idx.points:
                    if p.startswith("GZ "):
                        m.setdefault(p, cat["code"])
    return m


def silver(t: dict, r2c: dict[str, str]) -> str | None:
    if t.get("kind") != "complaint":
        return None
    v = t.get("verdict")
    if v in ("appeal_approved", "appeal_rejected"):
        return "complaint.admin"
    if v == "invalid_format":
        return "complaint.invalid"
    if v not in ("approved", "already_punished"):
        return None  # rejected / need_evidence / other: the admin outcome doesn't tell the category reliably
    viol = (t.get("violation") or "").strip()
    if not viol:
        return None
    for cid in load_rules().find_all(viol):
        if cid in r2c:
            return r2c[cid]
    low = viol.lower()
    for k, c in _SLANG.items():
        if re.search(rf"\b{k}\b", low):
            return c
    return None


def build() -> Path:
    r2c = rule_to_category()
    rows = [json.loads(l) for l in open(DATA / "tickets_valid.jsonl", encoding="utf-8")]
    out = DATA / "labels" / "silver_valid.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    cnt = collections.Counter()
    with open(out, "w", encoding="utf-8") as f:
        for t in rows:
            c = silver(t, r2c)
            if c:
                n += 1
                cnt[c] += 1
                f.write(json.dumps({"ticket_id": t["ticket_id"], "category": c}, ensure_ascii=False) + "\n")
    comp = sum(1 for t in rows if t["kind"] == "complaint")
    print(f"silver labels: {n} of {comp} valid complaints ({n / comp:.0%}) -> {out}")
    print(dict(cnt.most_common()))
    return out


def score(run_path: str) -> None:
    gold = {json.loads(l)["ticket_id"]: json.loads(l)["category"]
            for l in open(DATA / "labels" / "silver_valid.jsonl", encoding="utf-8")}
    hit = n = 0
    conf = collections.Counter()
    for l in open(run_path, encoding="utf-8"):
        r = json.loads(l)
        tid = r.get("ticket_id")
        pred = (r.get("ours") or r).get("category")
        if tid in gold:
            n += 1
            hit += pred == gold[tid]
            if pred != gold[tid]:
                conf[(gold[tid], pred)] += 1
    print(f"category accuracy vs silver: {hit}/{n} = {hit / max(n, 1):.0%}")
    rows = [json.loads(l) for l in open(run_path, encoding="utf-8")]
    llm = [(r["meta"].get("llm_category"), gold[r["ticket_id"]]) for r in rows
           if r.get("ticket_id") in gold and (r.get("meta") or {}).get("llm_category")]
    if llm:
        print(f"  LLM alone (where it answered): {sum(a == b for a, b in llm)}/{len(llm)} = "
              f"{sum(a == b for a, b in llm) / len(llm):.0%}")
    for (g, p), k in conf.most_common(8):
        print(f"  silver {g:<18} predicted {p:<18} x{k}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--score")
    a = ap.parse_args()
    if not (DATA / "labels" / "silver_valid.jsonl").exists() or not a.score:
        build()
    if a.score:
        score(a.score)
