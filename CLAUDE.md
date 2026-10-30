# CLAUDE.md — rules for every AI assistant working in this repo

Project: **Support Copilot** — Decentrathon 6.0 × Grand Mobile, case 03.
Team of 4. Each person works with their own Claude. Read this file fully before doing anything.

## Read first
1. `docs/TEAM.md` — roles, file ownership, task cards, timeline.
2. `docs/API.md` + `contract/schemas.py` — frozen API contract.
3. `docs/ARCHITECTURE.md` — pipeline and design decisions.

## Hard rules (never break)
1. **Never commit case data.** Nothing from `data/`, no `*.jsonl`, no copied ticket texts, no
   excerpts of `rules.json` / `taxonomy.json` in code, tests, docs or fixtures. This is a legal
   condition of the case. Test fixtures must be **invented** examples. Run `git status` before every commit.
2. **Never commit secrets.** API keys go to `.env` (git-ignored). Keep `.env.example` with empty values.
3. **Edit only files your role owns** (table in `docs/TEAM.md`). Need a change elsewhere → tell your
   human, who asks the owner. Never edit `contract/` unless you are the Tech Lead.
4. **Work in a branch** `feat/<area>-<short>`, open a PR to `main`. Never push to `main` directly.
   Before pushing: `git fetch origin && git rebase origin/main`.
5. **Don't fake results.** No hardcoded "AI" answers, no dead buttons, no empty functions pretending to
   work. Temporary stubs must return valid schema objects and be marked with `# STUB` and reason `"stub"`.
   Mock data lives only in clearly named `mocks/` folders.
6. **Verify before claiming done.** Run the code / tests and report what actually happened.

## Layout & conventions
- Python ≥ 3.11. Run everything **from the repo root**; imports are absolute:
  `from contract.schemas import TriageRequest`, `from service.app.pipeline import triage`.
- Service entry point: `uvicorn service.app.main:app --port 8000`.
- Pipeline entry point (owned by Tech Lead): `service.app.pipeline.triage(req: TriageRequest) -> TriageResponse`.
  Backend calls it; never reimplements it.
- Python deps: `service/requirements.txt` (Backend owns), `ml/requirements.txt` (Infra/QA owns).
- Data lives locally in `data/` (git-ignored): `data/tickets_train.jsonl.gz`, `data/tickets_valid.jsonl`,
  `data/rules.json`, `data/taxonomy.json`, `data/README.md`. Code reads paths from env `DATA_DIR` (default `data`).
- Code, comments, docs: English. Talking to your human: Russian.

## Domain facts worth knowing
- Two ticket kinds: `complaint` (from launcher, against a player) and `report` (short in-game request).
- Languages: mostly ru; uz (Cyrillic and Latin), kk, some en/pt. kz-server text uses cp1251 look-alikes:
  ќ→қ, ґ→ғ, ѕ→ң, є→ә, ј→ө, ў→ұ, ї→ү.
- Player claims are not facts. Drafts never promise unban, refund, or punishment of another player.
- Payments, ban appeals, account hacking always → `needs_human: true`.
- Cited rule ids must exist in `rules.json` — an invented rule zeroes the ticket in scoring.
