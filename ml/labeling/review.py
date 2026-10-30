"""Step 2 of the gold set: a human checks every proposal in the terminal. Owner: Infra / QA.

  python -m ml.labeling.review [--reviewer NAME]

Shows the ticket (player side only) and the LLM proposal. Answer with one line:
  Enter        accept category and priority as proposed
  5            category #5 from the list, keep priority        p1     keep category, priority p1
  5 p1         category #5 and priority p1                     s      skip (comes back next session)
  u            undo the last saved answer                      ?      category / priority criteria
  q            quit (everything answered is already saved)

Each answer is appended to data/gold.jsonl right away, so you can stop and continue any time.
gold.jsonl line: {ticket_id, kind, lang, category, priority, proposed_category, proposed_priority, changed, reviewer, at}
"""
from __future__ import annotations

import argparse
import json
import os
import textwrap
import time
from pathlib import Path

from ml.labeling.propose import GUIDE, PRIORITIES, PROPOSALS, read_jsonl, ticket_view
from service.app.llm.prompt import load_taxonomy

DATA = Path(os.getenv("DATA_DIR", "data"))
GOLD = DATA / "gold.jsonl"


def wrap(s: str, indent: str = "    ") -> str:
    return textwrap.fill(s, width=110, initial_indent=indent, subsequent_indent=indent)


def show_help(cats: list[dict], tax: dict) -> None:
    for i, c in enumerate(cats, 1):
        print(f" {i:>2}. {c['code']:<20} {c.get('title', '')}")
        if c.get("examples"):
            print(wrap("e.g. " + "; ".join(map(str, c["examples"][:3])), "       "))
    print()
    for p in tax["priorities"]:
        print(f"  {p['code']}  {p.get('title', '')}: {p.get('rule', '')}")
    print("\nTEAM GUIDE (unsure for more than 10 s -> s):\n" + GUIDE)


def parse(answer: str, cats: list[dict], cur_cat: str | None, cur_pri: str | None):
    """-> (category, priority) or None if the answer is not understood."""
    cat, pri = cur_cat, cur_pri
    for tok in answer.split():
        if tok.lower() in PRIORITIES:
            pri = tok.lower()
        elif tok.isdigit() and 1 <= int(tok) <= len(cats):
            cat = cats[int(tok) - 1]["code"]
        elif tok in {c["code"] for c in cats}:
            cat = tok
        else:
            return None
    return (cat, pri) if cat and pri else None


def save_all(rows: list[dict]) -> None:
    tmp = GOLD.with_name(GOLD.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    os.replace(tmp, GOLD)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", default=str(DATA / "tickets_valid.jsonl"))
    ap.add_argument("--reviewer", default=os.getenv("USER") or os.getenv("USERNAME") or "unknown")
    a = ap.parse_args()

    if not PROPOSALS.exists():
        raise SystemExit(f"{PROPOSALS} not found — run: python -m ml.labeling.propose")
    tax = load_taxonomy()
    tickets = {t["ticket_id"]: t for t in read_jsonl(Path(a.file))}
    proposals = read_jsonl(PROPOSALS)
    gold = read_jsonl(GOLD) if GOLD.exists() else []
    skipped: set[str] = set()

    while True:
        done = {g["ticket_id"] for g in gold}
        todo = [p for p in proposals if p["ticket_id"] not in done and p["ticket_id"] not in skipped]
        if not todo:
            break
        p = todo[0]
        t = tickets[p["ticket_id"]]
        cats = [c for c in tax["categories"] if c["kind"] == t["kind"]]
        v = ticket_view(t)

        print("\n" + "=" * 110)
        print(f"[{len(done) + 1}/{len(proposals)}]  {t['kind'].upper()}  lang={t.get('lang')}  id={t['ticket_id']}")
        for k in ("reported_nick", "rule_refs_raw", "server_lang", "region"):
            if v.get(k):
                print(f"  {k}: {v[k]}")
        print("  player:")
        print(wrap(v["player"] or "(empty)"))
        if v.get("reported_player"):
            print("  reported player:")
            print(wrap(v["reported_player"]))
        print("-" * 110)
        print("  " + "   ".join(f"{i}.{c['code'].split('.', 1)[1]}" for i, c in enumerate(cats, 1)))
        if p.get("category"):
            print(f"  PROPOSED: {p['category']}  {p['priority']}   ({p.get('reason') or ''})")
        else:
            print("  PROPOSED: none — type a category number and priority, e.g. 3 p2")

        while True:
            ans = input("  Enter=accept | N | pN | N pN | s | u | ? | q > ").strip().lstrip("﻿")
            if ans == "q":
                print(f"saved {len(gold)} answers -> {GOLD}")
                return
            if ans == "?":
                show_help(cats, tax)
                continue
            if ans == "s":
                skipped.add(p["ticket_id"])
                break
            if ans == "u":
                if gold:
                    last = gold.pop()
                    save_all(gold)
                    skipped.discard(last["ticket_id"])
                    print(f"  undone: {last['ticket_id']}")
                break
            res = parse(ans, cats, p.get("category"), p.get("priority"))
            if res is None:
                print("  not understood, '?' shows the lists")
                continue
            cat, pri = res
            row = {"ticket_id": t["ticket_id"], "kind": t["kind"], "lang": t.get("lang"),
                   "category": cat, "priority": pri,
                   "proposed_category": p.get("category"), "proposed_priority": p.get("priority"),
                   "changed": (cat, pri) != (p.get("category"), p.get("priority")),
                   "reviewer": a.reviewer, "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
            gold.append(row)
            with open(GOLD, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            break

    n = len(gold)
    changed = sum(g["changed"] for g in gold)
    print(f"\ndone: {n} reviewed, {len(skipped)} skipped this session -> {GOLD}")
    if n:
        print(f"LLM proposal kept as is: {n - changed}/{n} ({(n - changed) / n:.0%})")


if __name__ == "__main__":
    main()
