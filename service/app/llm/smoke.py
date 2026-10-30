"""Smoke run of the full pipeline on N validation tickets. Owner: Tech Lead.

  python -m service.app.llm.smoke --n 40

Prints a summary and writes every result to data/runs/<timestamp>.jsonl (local only, git-ignored —
contains case data). Stratified: all non-ru languages first, then ru, so kk/uz are always covered.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import random
import time
from pathlib import Path

from contract.schemas import TriageRequest
from service.app.pipeline import triage_with_meta


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--file", default=os.path.join(os.getenv("DATA_DIR", "data"), "tickets_valid.jsonl"))
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--sleep", type=float, default=0.0, help="pause between calls (free-tier rate limits)")
    ap.add_argument("--langs", default="", help="only these data langs, e.g. kk,uz,lat")
    a = ap.parse_args()

    if a.file.endswith(".json"):
        rows = json.load(open(a.file, encoding="utf-8"))
    else:
        rows = [json.loads(l) for l in open(a.file, encoding="utf-8")]
    random.Random(a.seed).shuffle(rows)
    if a.langs:
        keep = set(a.langs.split(","))
        rows = [r for r in rows if r.get("lang") in keep]
    non_ru = [r for r in rows if r.get("lang") not in ("ru",)]
    ru = [r for r in rows if r.get("lang") == "ru"]
    sample = (non_ru[: a.n // 2] + ru)[: a.n] if not a.file.endswith(".json") else rows[: a.n]

    out_dir = Path(os.getenv("DATA_DIR", "data")) / "runs"
    out_dir.mkdir(parents=True, exist_ok=True)
    tag = (os.getenv("LLM_MODEL", "") + ("_" + a.langs.replace(",", "-") if a.langs else "")).replace("/", "-")
    out_path = out_dir / f"smoke_{time.strftime('%Y%m%d_%H%M%S')}_{tag}.jsonl"

    lat, cost, tin, tout = [], [], [], []
    ok = nh = drafts = dropped = invalid = 0
    errors = collections.Counter()
    cats = collections.Counter()
    with open(out_path, "w", encoding="utf-8") as f:
        for i, t in enumerate(sample, 1):
            req = TriageRequest.model_validate({**t, "messages": [m for m in t["messages"] if m["role"] != "admin"]})
            resp, meta = triage_with_meta(req)
            lat.append(meta.latency_s)
            if meta.llm_ok:
                ok += 1; cost.append(meta.cost_usd); tin.append(meta.tokens_in); tout.append(meta.tokens_out)
            else:
                errors[(meta.error or "")[:80]] += 1
            nh += resp.needs_human; drafts += resp.draft_reply is not None
            dropped += len(meta.dropped_citations); invalid += len(meta.invalid_mentions)
            cats[resp.category] += 1
            f.write(json.dumps({
                "ticket_id": t["ticket_id"], "kind": t["kind"], "data_lang": t.get("lang"),
                "player": " ".join(m["text"] for m in t["messages"] if m["role"] == "player")[:500],
                "real_admin": " ".join(m["text"] for m in t["messages"] if m["role"] == "admin")[:500],
                "real_verdict": t.get("verdict"),
                "ours": resp.model_dump(), "meta": meta.__dict__}, ensure_ascii=False) + "\n")
            print(f"[{i}/{len(sample)}] {t['kind']:<9} {t.get('lang'):<4} -> {resp.category:<18} {resp.priority} "
                  f"human={resp.needs_human!s:<5} {meta.latency_s:.2f}s {'OK' if meta.llm_ok else 'ERR ' + (meta.error or '')[:60]}")
            if a.sleep:
                time.sleep(a.sleep)

    n = len(sample)
    lat.sort()
    pct = lambda xs, q: xs[min(len(xs) - 1, int(q * len(xs)))] if xs else 0
    print("\n===== SUMMARY =====")
    print(f"tickets: {n} | LLM ok: {ok}/{n} | drafts: {drafts} | needs_human: {nh / n:.0%}")
    print(f"latency p50 {pct(lat, .5):.2f}s  p95 {pct(lat, .95):.2f}s")
    if cost:
        print(f"tokens in/out avg {sum(tin) / len(tin):.0f}/{sum(tout) / len(tout):.0f} | "
              f"cost/ticket avg ${sum(cost) / len(cost):.5f} (check LLM_PRICE_* in .env)")
    print(f"citations dropped by validator: {dropped} | invented rules stripped from text: {invalid}")
    print("categories:", dict(cats.most_common()))
    if errors:
        print("errors:", dict(errors.most_common(5)))
    print(f"\nfull results: {out_path}")


if __name__ == "__main__":
    main()
