"""Measure Backend preprocessing on the local case data. Owner: Backend.

  python -m service.scripts.measure_preprocess                 # all three measurements, numbers only
  python -m service.scripts.measure_preprocess --only lang --examples 20   # + local mismatch samples (console only)

Prints aggregate numbers only. --examples prints ticket texts to the local console for error analysis —
never paste that output into git, PRs or docs (case data policy). Writes nothing to disk.

1. lang:   detect_lang vs the `lang` field of tickets_valid.jsonl, new module vs the TEMP fallback in pipeline.py,
           both wrapped exactly like the pipeline does (normalize -> detect -> pipeline's region overrides).
2. dedup:  share of train tickets flagged by dedup.check (processed in created_at order) vs the plain
           "same reporter within 60 min" repeat rate (the ~24% from docs/EDA.md).
3. guards: how often guards.check fires on train, by reason and by kind.
"""
from __future__ import annotations

import argparse
import collections
import gzip
import json
import os
import re
import textwrap
import time
from datetime import timedelta
from pathlib import Path

from contract.schemas import TriageRequest
from service.app import dedup, guards
from service.app.normalize import detect_lang, normalize

DATA = Path(os.getenv("DATA_DIR", "data"))
CONTRACT = ("ru", "kk", "uz", "en", "und")


def read(path: Path) -> list[dict]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def player(t: dict) -> str:
    return " ".join(m["text"] for m in t.get("messages", []) if m.get("role") == "player").strip()


def to_request(t: dict) -> TriageRequest:
    fields = set(TriageRequest.model_fields)
    msgs = [{"role": m["role"], "text": m.get("text") or "", "attachments": m.get("attachments") or 0}
            for m in t.get("messages", []) if m.get("role") in ("player", "reported_player", "admin")]
    body = {k: v for k, v in t.items() if k in fields and k != "messages"}
    for k in ("ticket_id", "reporter_hash", "reported_nick", "rule_refs_raw", "created_at", "server_lang", "region"):
        if body.get(k) is not None:
            body[k] = str(body[k])
    return TriageRequest(**body, messages=msgs or [{"role": "player", "text": ""}])


def fallback_lang():
    """The TEMP fallback from pipeline.py, extracted verbatim (it is dead code once normalize.py exists)."""
    src = Path("service/app/pipeline.py").read_text(encoding="utf-8")
    block = src.split("except Exception:  # TEMP fallback until Backend's normalize.py lands", 1)[1]
    block = block.split("try:  # pragma: no cover", 1)[0]
    ns: dict = {"re": re}
    exec(textwrap.dedent(block), ns)  # noqa: S102 — our own repo file
    return ns["_normalize"], ns["_detect_lang"]


def pipeline_lang(norm, detect, raw: str, region, server_lang) -> str:
    """Language exactly as pipeline.triage_with_meta computes it (incl. its region overrides)."""
    text = norm(raw, region, server_lang) if raw else raw
    lang = detect(text, region, server_lang) if text else "und"
    if lang == "ru" and (region == "kz" or server_lang == "kk") and re.search(r"[әғқңөұүһі]", text.lower()) \
            and not re.search(r"[їєґ]", raw.lower()):
        lang = "kk"
    if lang == "kk" and region == "uz" and not re.search(r"[әңөұүһі]", text.lower()):
        lang = "uz"
    return lang


def pct(a: int, b: int) -> str:
    return f"{100 * a / b:.1f}%" if b else "n/a"


def measure_lang(examples: int) -> None:
    rows = read(DATA / "tickets_valid.jsonl")
    fb_norm, fb_detect = fallback_lang()
    systems = {"fallback": (fb_norm, fb_detect), "new": (normalize, detect_lang)}
    gold_cnt = collections.Counter()
    hit = {s: collections.Counter() for s in systems}
    conf = {s: collections.Counter() for s in systems}
    diffs = []
    for t in rows:
        gold = t.get("lang") or "und"
        raw = player(t)
        gold_cnt[gold] += 1
        preds = {s: pipeline_lang(n, d, raw, t.get("region"), t.get("server_lang")) for s, (n, d) in systems.items()}
        for s, p in preds.items():
            hit[s][gold] += p == gold
            conf[s][(gold, p)] += 1
        if preds["new"] != gold:
            diffs.append((gold, preds["new"], preds["fallback"], t.get("region"), raw))

    n = len(rows)
    in_contract = sum(c for g, c in gold_cnt.items() if g in CONTRACT)
    print(f"\n== detect_lang on tickets_valid.jsonl: {n} tickets ({in_contract} with a label in the contract set)")
    print(f"{'label':6} {'n':>6} " + " ".join(f"{s:>16}" for s in systems))
    for g, c in gold_cnt.most_common():
        print(f"{g:6} {c:6} " + " ".join(f"{hit[s][g]:6} = {pct(hit[s][g], c):>7}" for s in systems))
    for title, keep in (("ALL labels", lambda g: True), ("contract labels", lambda g: g in CONTRACT)):
        tot = sum(c for g, c in gold_cnt.items() if keep(g))
        print(f"{title:16} " + " ".join(f"{s}={pct(sum(v for g, v in hit[s].items() if keep(g)), tot)}"
                                        for s in systems))
    for s in systems:
        print(f"\nconfusion [{s}] label -> predicted (count):")
        for g, _ in gold_cnt.most_common():
            row = {p: c for (gg, p), c in conf[s].items() if gg == g}
            print(f"  {g:6} " + ", ".join(f"{p}:{c}" for p, c in sorted(row.items(), key=lambda kv: -kv[1])))
    if examples:
        print(f"\n-- {min(examples, len(diffs))} mismatch samples (LOCAL ONLY, do not publish) --")
        by_pair = collections.defaultdict(list)
        for d in diffs:
            by_pair[(d[0], d[1])].append(d)
        for pair, items in sorted(by_pair.items(), key=lambda kv: -len(kv[1])):
            print(f"[label={pair[0]} new={pair[1]}] x{len(items)}")
            for g, p, fb, region, raw in items[: max(1, examples // max(1, len(by_pair)))]:
                print(f"    fb={fb} region={region} | {raw[:120]!r}")


def measure_dedup() -> None:
    rows = read(DATA / "tickets_train.jsonl.gz")
    reqs = [to_request(t) for t in rows]
    with_hash = [r for r in reqs if r.reporter_hash]
    with_hash.sort(key=lambda r: dedup.parse_time(r.created_at))
    # baseline from EDA: same reporter within 60 min of the previous ticket, text ignored
    last: dict[str, object] = {}
    repeats = collections.Counter()
    for r in with_hash:
        at = dedup.parse_time(r.created_at)
        prev = last.get(r.reporter_hash)
        if prev is not None and at - prev <= timedelta(minutes=60):
            repeats[r.kind] += 1
        last[r.reporter_hash] = at
    dedup.reset()
    flagged = collections.Counter()
    t0 = time.time()
    for r in with_hash:
        if dedup.check(r):
            flagged[r.kind] += 1
    dt = time.time() - t0
    dedup.reset()
    kinds = collections.Counter(r.kind for r in with_hash)
    n = len(with_hash)
    print(f"\n== dedup on tickets_train.jsonl.gz: {len(reqs)} tickets, {n} with reporter_hash"
          f" ({sum(1 for r in reqs if r.created_at)} with created_at)")
    print(f"same reporter <=60 min (EDA baseline): {sum(repeats.values())} = {pct(sum(repeats.values()), n)}  "
          + ", ".join(f"{k} {pct(repeats[k], kinds[k])}" for k in sorted(kinds)))
    print(f"dedup.check flagged (+ similar text):  {sum(flagged.values())} = {pct(sum(flagged.values()), n)}  "
          + ", ".join(f"{k} {pct(flagged[k], kinds[k])}" for k in sorted(kinds)))
    print(f"share of same-reporter repeats that dedup flags: {pct(sum(flagged.values()), sum(repeats.values()))}")
    print(f"speed: {1000 * dt / max(1, n):.3f} ms/ticket")
    flagged_ids = set()
    for r in with_hash:
        if dedup.check(r):
            flagged_ids.add(r.ticket_id)
    dedup.reset()
    _by_verdict(rows, lambda t: str(t["ticket_id"]) in flagged_ids, "dedup flagged")


def _by_verdict(rows: list[dict], pred, title: str) -> None:
    """Rate per admin verdict for complaints (appeal_* / duplicate verdicts serve as a reference signal)."""
    tot, hit = collections.Counter(), collections.Counter()
    for t in rows:
        if t.get("kind") != "complaint":
            continue
        v = t.get("verdict") or "none"
        v = "appeal_*" if v.startswith("appeal") else v
        tot[v] += 1
        hit[v] += bool(pred(t))
    print(f"{title} by complaint verdict: " + ", ".join(f"{v} {pct(hit[v], c)} (n={c})" for v, c in tot.most_common()))


def measure_guards() -> None:
    rows = read(DATA / "tickets_train.jsonl.gz")
    kinds = collections.Counter()
    any_hit = collections.Counter()
    by_reason = collections.Counter()
    by_reason_kind = collections.Counter()
    for t in rows:
        r = to_request(t)
        kinds[r.kind] += 1
        reasons = guards.check(r, "")  # text-only: category comes from the LLM at runtime
        if reasons:
            any_hit[r.kind] += 1
        for x in reasons:
            topic = x.split(": ", 1)[1].split(" —")[0]
            by_reason[topic] += 1
            by_reason_kind[(topic, r.kind)] += 1
    n = len(rows)
    print(f"\n== guards on tickets_train.jsonl.gz: {n} tickets")
    _by_verdict(rows, lambda t: bool(guards.check(to_request(t), "")), "any guard reason")
    print(f"any reason: {sum(any_hit.values())} = {pct(sum(any_hit.values()), n)}  "
          + ", ".join(f"{k} {pct(any_hit[k], kinds[k])}" for k in sorted(kinds)))
    for topic, c in by_reason.most_common():
        print(f"  {topic:40} {c:6} = {pct(c, n):>6}  "
              + ", ".join(f"{k} {pct(by_reason_kind[(topic, k)], kinds[k])}" for k in sorted(kinds)))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["lang", "dedup", "guards"])
    ap.add_argument("--examples", type=int, default=0, help="print N local mismatch samples (lang only)")
    a = ap.parse_args()
    if a.only in (None, "lang"):
        measure_lang(a.examples)
    if a.only in (None, "dedup"):
        measure_dedup()
    if a.only in (None, "guards"):
        measure_guards()


if __name__ == "__main__":
    main()
