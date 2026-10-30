"""Step 1 of the gold set: the LLM proposes category + priority for a sample of validation tickets. Owner: Infra / QA.

  python -m ml.labeling.propose [--n 200] [--reports 80] [--batch 15] [--sleep 5]

- Sample: --n tickets from data/tickets_valid.jsonl, --reports of them reports (we have no labels for reports at
  all), the rest complaints; inside each kind, languages are spread evenly (round-robin), so uz/kk/lat are covered.
  Fixed seed, so the sample is the same on every machine.
- One LLM request labels a whole batch (10–20 tickets) to save the free-tier quota (~15 req/min, ~1000/day).
- Every answer is cached on disk (data/labels/cache/), so a rerun or a crash never pays twice.
- Criteria come from data/taxonomy.json at runtime; nothing from the case is hardcoded here.
- Only the player's side is shown to the LLM: the admin reply/verdict is not a category label
  (e.g. an "invalid format" verdict does not make a ticket complaint.invalid).

Output: data/labels/gold_proposals.jsonl, one line per sampled ticket (local only, never commit).
Next step: python -m ml.labeling.review
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import time
from collections import defaultdict
from pathlib import Path

from service.app.llm.prompt import load_taxonomy
from service.app.llm.provider import LLMError, complete_json

DATA = Path(os.getenv("DATA_DIR", "data"))
LABELS = DATA / "labels"
PROPOSALS = LABELS / "gold_proposals.jsonl"
PRIORITIES = ("p0", "p1", "p2", "p3")
MAX_TEXT = 700

# Team labeling guide (agreed with the Tech Lead) — shown to the LLM and to the human reviewer ('?').
GUIDE = """Reports:
- "помогите", "сос", "help", "yordam", "застрял", "переверните", "тп" -> report.help (even with no details)
- player id/nick + "дм", "оск", "читер" (e.g. "345 дм") -> report.complaint
- "как ...?", "где ...?", "сколько ...?" -> report.question
- hacking, lost property, no access -> report.account (p0)
- donation, payment, "не пришло" -> report.payment (p0)
- report.offtopic ONLY for greetings, single symbols ("+", "."), nonsense, insults to admins
Complaints:
- "оск", "оск родни", swearing in chat -> complaint.insult
- "дм", "убил без причины", "в ЗЗ" -> complaint.dm
- "не согласен с наказанием", "админ дал бан ни за что", "прошу пересмотреть" -> complaint.admin
- complaint.invalid ONLY if by meaning it is not a complaint: a question, thanks, "жб", a lone link with no words.
  Short "оск"/"дм" is a normal complaint.
Priorities:
- p0 only hacking/access, payments, real-life threats
- p1 cheats, scam, ads for real money, complaint about an admin
- p2 insults, DM, RP violations, complaints sent as a report
- p3 questions, help, offtopic, invalid"""

SYSTEM = f"""You label support tickets of an online role-play game for a hand-checked test set.
For every ticket choose exactly one category of its kind and one priority, by the taxonomy given and the team
guide below. Judge by the meaning of the player's text only; player claims are not facts, but the category is
what the player complains about / asks for.

{GUIDE}

reason: one short phrase in English explaining the choice.
Return JSON {{"labels": [{{"ticket_id", "category", "priority", "reason"}}, ...]}} with one item per input ticket."""

SCHEMA = {
    "type": "OBJECT",
    "properties": {"labels": {"type": "ARRAY", "items": {
        "type": "OBJECT",
        "properties": {"ticket_id": {"type": "STRING"}, "category": {"type": "STRING"},
                       "priority": {"type": "STRING", "enum": list(PRIORITIES)}, "reason": {"type": "STRING"}},
        "required": ["ticket_id", "category", "priority", "reason"]}}},
    "required": ["labels"],
}


def read_jsonl(path: Path) -> list[dict]:
    with open(path, encoding="utf-8-sig") as f:
        return [json.loads(line) for line in f if line.strip()]


def ticket_view(t: dict) -> dict:
    """What a human labeler would see: player side + form fields, no admin reply."""
    by_role = defaultdict(list)
    for m in t.get("messages", []):
        by_role[m.get("role")].append(m.get("text") or "")
    v = {"ticket_id": t["ticket_id"], "kind": t["kind"],
         "player": " | ".join(by_role["player"])[:MAX_TEXT]}
    if by_role["reported_player"]:
        v["reported_player"] = " | ".join(by_role["reported_player"])[:MAX_TEXT]
    for k in ("reported_nick", "rule_refs_raw", "server_lang", "region"):
        if t.get(k):
            v[k] = t[k]
    return v


def sample(rows: list[dict], n: int, n_reports: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    out = []
    for kind, k in (("report", n_reports), ("complaint", n - n_reports)):
        by_lang = defaultdict(list)
        for t in rows:
            if t["kind"] == kind:
                by_lang[t.get("lang") or "und"].append(t)
        for lst in by_lang.values():
            rng.shuffle(lst)
        picked = []
        while len(picked) < k and any(by_lang.values()):
            for lang in sorted(by_lang):  # round-robin over languages
                if by_lang[lang] and len(picked) < k:
                    picked.append(by_lang[lang].pop())
        out += picked
    rng.shuffle(out)
    return out


def taxonomy_for(kind: str) -> str:
    t = load_taxonomy()
    cats = [{k: v for k, v in c.items() if k != "rules"} for c in t["categories"] if c["kind"] == kind]
    return json.dumps({"categories": cats, "priorities": t["priorities"]}, ensure_ascii=False, indent=1)


def label_batch(batch: list[dict], args) -> tuple[dict[str, dict], float]:
    """Returns ({ticket_id: label}, cost_usd). Cached by the exact request, so reruns are free."""
    kind = batch[0]["kind"]
    user = (f"TAXONOMY (kind={kind}):\n{taxonomy_for(kind)}\n\nTICKETS:\n"
            + "\n".join(json.dumps(ticket_view(t), ensure_ascii=False) for t in batch))
    model = os.getenv("LLM_MODEL", "")
    key = hashlib.sha256(f"{model}\n{SYSTEM}\n{user}".encode("utf-8")).hexdigest()[:24]
    cache = LABELS / "cache" / f"{key}.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))["labels"], 0.0

    for attempt in range(args.retries + 1):
        try:
            res = complete_json(SYSTEM, user, max_tokens=120 * len(batch) + 200, schema=SCHEMA, timeout=args.timeout)
            items = json.loads(res.text)["labels"]
            break
        except (LLMError, json.JSONDecodeError, KeyError, TypeError) as e:
            if attempt == args.retries:
                raise
            wait = 60 if "429" in str(e) else 10
            print(f"  retry in {wait}s after: {str(e)[:120]}", flush=True)
            time.sleep(wait)
    codes = {c["code"] for c in load_taxonomy()["categories"] if c["kind"] == kind}
    labels = {}
    for it in items:
        tid = str(it.get("ticket_id"))
        if it.get("category") in codes and it.get("priority") in PRIORITIES:
            labels[tid] = {"category": it["category"], "priority": it["priority"], "reason": it.get("reason", "")}
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"model": model, "labels": labels, "cost_usd": res.cost_usd}, ensure_ascii=False),
                     encoding="utf-8")
    return labels, res.cost_usd


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", default=str(DATA / "tickets_valid.jsonl"))
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--reports", type=int, default=80, help="how many of --n are reports")
    ap.add_argument("--batch", type=int, default=15)
    ap.add_argument("--sleep", type=float, default=5.0, help="pause after each real LLM call")
    ap.add_argument("--timeout", type=float, default=90.0)
    ap.add_argument("--retries", type=int, default=2)
    ap.add_argument("--seed", type=int, default=13)
    a = ap.parse_args()

    tickets = sample(read_jsonl(Path(a.file)), a.n, a.reports, a.seed)
    batches = []
    for kind in ("report", "complaint"):
        same = [t for t in tickets if t["kind"] == kind]
        k = -(-len(same) // a.batch)  # equal-sized batches: no 1-ticket tail request
        batches += [same[i::k] for i in range(k)]
    print(f"{len(tickets)} tickets ({sum(t['kind'] == 'report' for t in tickets)} reports) in {len(batches)} "
          f"LLM requests | model={os.getenv('LLM_MODEL')}", flush=True)

    labels: dict[str, dict] = {}
    cost = 0.0
    failed = 0
    for i, b in enumerate(batches, 1):
        try:
            got, c = label_batch(b, a)
        except (LLMError, json.JSONDecodeError, KeyError, TypeError) as e:
            failed += 1
            print(f"[{i}/{len(batches)}] FAILED: {str(e)[:160]}", flush=True)
            continue
        labels.update(got)
        cost += c
        print(f"[{i}/{len(batches)}] {b[0]['kind']:<9} labeled {len(got)}/{len(b)} "
              f"{'(cache)' if c == 0 else f'${c:.4f}'}", flush=True)
        if c and a.sleep and i < len(batches):
            time.sleep(a.sleep)

    LABELS.mkdir(parents=True, exist_ok=True)
    with open(PROPOSALS, "w", encoding="utf-8") as f:
        for t in tickets:
            lab = labels.get(t["ticket_id"])
            f.write(json.dumps({"ticket_id": t["ticket_id"], "kind": t["kind"], "lang": t.get("lang"),
                                "category": lab and lab["category"], "priority": lab and lab["priority"],
                                "reason": lab and lab["reason"]}, ensure_ascii=False) + "\n")
    missing = sum(t["ticket_id"] not in labels for t in tickets)
    print(f"\nproposals: {len(tickets) - missing}/{len(tickets)} labeled, {missing} without a proposal "
          f"(the reviewer labels them by hand), failed requests: {failed}, new LLM cost ${cost:.4f}")
    print(f"-> {PROPOSALS}\nnext: python -m ml.labeling.review")


if __name__ == "__main__":
    main()
