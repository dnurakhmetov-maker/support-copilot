"""Batch prediction over a tickets file. Owner: Infra / QA.

  python -m eval.predict --in data/tickets_valid.jsonl --out data/runs/pred.jsonl [--limit N] [--sleep S] [--resume]

Writes two files, one line per input ticket, in input order:
  <out>              {"ticket_id": ..., ...TriageResponse}            (the format from docs/API.md)
  <out>.meta.jsonl   {"ticket_id": ..., latency_s, tokens_in, tokens_out, cost_usd, llm_ok, error, ...}

LLM calls are rate limited on the free tier (~15/min, ~1000/day per key): use --sleep 4 for real runs and
--resume to continue an interrupted run (tickets whose previous answer came from the LLM are not re-sent).
LLM_PROVIDER=none runs the whole chain without the LLM (every ticket degrades to "needs human").
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path

from pydantic import ValidationError

from contract.schemas import Extracted, TriageRequest, TriageResponse
from service.app.pipeline import TriageMeta, triage_with_meta


def meta_path(out: Path) -> Path:
    stem = out.name[: -len(".jsonl")] if out.name.endswith(".jsonl") else out.name
    return out.with_name(stem + ".meta.jsonl")


def read_jsonl(path: Path) -> list[dict]:
    with open(path, encoding="utf-8-sig") as f:  # -sig: files saved by Windows tools may start with a BOM
        return [json.loads(line) for line in f if line.strip()]


def tmp_paths(out: Path) -> tuple[Path, Path]:
    return out.with_name(out.name + ".tmp"), meta_path(out).with_name(meta_path(out).name + ".tmp")


def load_done(out: Path) -> dict[str, tuple[dict, dict]]:
    """Previous answers that came from the LLM, keyed by ticket_id (for --resume).
    Reads the finished run and the *.tmp files of an interrupted one."""
    done = {}
    for po, pm in [(out, meta_path(out)), tmp_paths(out)]:
        if not po.exists() or not pm.exists():
            continue
        for pred, meta in zip(read_jsonl(po), read_jsonl(pm)):
            if meta.get("llm_ok") and pred.get("ticket_id") == meta.get("ticket_id"):
                done[pred["ticket_id"]] = (pred, meta)
    return done


def invalid_input(t: dict, error: str) -> tuple[TriageResponse, TriageMeta]:
    """A line that does not match TriageRequest still gets a schema-valid answer, so line order is kept."""
    kind = t.get("kind") if t.get("kind") in ("complaint", "report") else "report"
    resp = TriageResponse(
        lang="und", category="complaint.other" if kind == "complaint" else "report.question", priority="p2",
        is_spam_or_duplicate=False, extracted=Extracted(), draft_reply=None, needs_human=True, confidence=0.0,
        reasons=["некорректный формат тикета — требуется администратор"])
    return resp, TriageMeta(error=f"invalid input: {error[:280]}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in", dest="inp", required=True, help="input tickets .jsonl")
    ap.add_argument("--out", required=True, help="output predictions .jsonl")
    ap.add_argument("--limit", type=int, default=0, help="only the first N tickets (0 = all)")
    ap.add_argument("--sleep", type=float, default=0.0, help="pause after each LLM call, seconds")
    ap.add_argument("--resume", action="store_true", help="reuse LLM answers already in --out")
    a = ap.parse_args()

    tickets = read_jsonl(Path(a.inp))
    if a.limit:
        tickets = tickets[: a.limit]
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    done = load_done(out) if a.resume else {}
    provider = os.getenv("LLM_PROVIDER", "gemini")
    print(f"{len(tickets)} tickets from {a.inp} | LLM_PROVIDER={provider} | reused from previous run: "
          f"{sum(t.get('ticket_id') in done for t in tickets)}", flush=True)

    tmp_out, tmp_meta = tmp_paths(out)
    n = ok = reused = 0
    t_start = time.time()
    with open(tmp_out, "w", encoding="utf-8") as fo, open(tmp_meta, "w", encoding="utf-8") as fm:
        for t in tickets:
            tid = t.get("ticket_id")
            if tid in done:
                pred, meta = done[tid]
                reused += 1
            else:
                try:
                    req = TriageRequest.model_validate(
                        {**t, "messages": [m for m in t.get("messages", []) if m.get("role") != "admin"]})
                    resp, m = triage_with_meta(req)
                except ValidationError as e:
                    resp, m = invalid_input(t, str(e))
                pred = {"ticket_id": tid, **resp.model_dump(mode="json")}
                meta = {"ticket_id": tid, **asdict(m)}
                if m.llm_ok and a.sleep:
                    time.sleep(a.sleep)
            fo.write(json.dumps(pred, ensure_ascii=False) + "\n")
            fm.write(json.dumps(meta, ensure_ascii=False) + "\n")
            n += 1
            ok += bool(meta.get("llm_ok"))
            if n % 50 == 0 or n == len(tickets):
                fo.flush(); fm.flush()
                print(f"[{n}/{len(tickets)}] llm_ok={ok} reused={reused} elapsed={time.time() - t_start:.0f}s",
                      flush=True)
    # replace only after a complete run, so an interrupted run never truncates earlier results
    os.replace(tmp_out, out)
    os.replace(tmp_meta, meta_path(out))
    print(f"wrote {n} predictions -> {out}\nwrote {n} meta lines  -> {meta_path(out)}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit("interrupted: partial results are in the *.tmp files, the previous --out is untouched; "
                 "rerun with --resume to continue")
