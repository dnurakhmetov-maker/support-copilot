# Team plan

## Start here

Each participant: clone the repo, put case data into `data/` (never commit it), open Claude Code in the repo root
and paste your starter prompt from `docs/prompts/` (`backend.md`, `frontend.md`, `infra-qa.md`).
Repo-wide rules for every AI assistant are in `CLAUDE.md`.

## Principle

Walking skeleton first: by **H+6** the full chain works end to end on stubs
(`/triage` returns valid JSON, `make predict` runs, admin-ui shows the queue).
After that everyone improves **their own** module without breaking the chain.

H+0 = moment we receive the case data.

## Ownership (one owner per file — no parallel edits)

| Path | Owner |
|---|---|
| `contract/`, `service/app/pipeline.py`, `classify.py`, `rag/`, `llm/`, `validate.py` | Tech Lead (+ Claude) |
| `service/app/main.py`, `api/`, `normalize.py`, `dedup.py`, `guards.py`, storage | Backend |
| `admin-ui/` | Frontend |
| `ml/labeling/`, `eval/`, `Makefile`, `Dockerfile`, `docker-compose.yml`, `docs/REPORT.md` | Infra / QA |

---

## Participant 1 — Tech Lead (+ Claude)

**Goal:** the smart part — classification, RAG, draft generation, zero hallucinations.

1. Freeze contract (`contract/schemas.py`, `docs/API.md`) — **H+2**.
2. `pipeline.py` — orchestration with stubs, per-step latency — **H+4**.
3. `llm/` — provider abstraction (Gemini / Groq / local), prompt, JSON output, retries, timeout.
4. `rag/` — rules index by item id; index of past tickets with admin replies; hybrid retrieval.
5. `validate.py` — citations must exist and be retrieved; block promises of unban/refund/punishment.
6. `classify.py` — train on labels from Infra/QA, rule overrides for p0.

**Done when:** `make eval` shows 0 invalid citations, category/priority metrics on gold set, p95 < 5s.
**Integration:** merges all PRs into `main`.

## Participant 2 — Backend

**Goal:** reliable HTTP service and deterministic preprocessing.

1. FastAPI app: `POST /triage`, `GET /health` per `docs/API.md` — **H+4** (pipeline stubs ok).
2. Admin endpoints: `POST /tickets`, `GET /queue`, `GET /tickets/{id}`, `POST /tickets/{id}/action`, `GET /stats`; SQLite storage — **H+8**.
3. `normalize.py` — cp1251 look-alike map (table in data README), text cleanup, language detection.
4. `guards.py` — payments, ban appeals, account hacking, refunds → `needs_human=true` with reason.
5. `dedup.py` — same `reporter_hash` + similar text within 60 min → duplicate (bonus criterion).
6. Error handling per API.md; `/triage` never 500s because of LLM.

**Tech:** Python, FastAPI, pydantic, sqlite3, pytest.
**Done when:** pytest green; `curl` example from API.md returns schema-valid JSON; edits from admin are stored in history.
**Integration:** imports schemas from `contract/`, never edits them. PR from `feat/backend-*`.

## Participant 3 — Frontend

**Goal:** admin interface that makes the demo convincing.

1. Next.js + TypeScript + Tailwind app in `admin-ui/`.
2. Queue page: tickets sorted by priority, filters by priority/category/status, p0 highlighted.
3. Ticket page: player messages, extracted facts, category/priority, draft with **highlighted rule citations** (hover → rule text), `needs_human` reasons.
4. Actions: Send / Edit (inline editor) / Escalate → `POST /tickets/{id}/action`.
5. Stats page: `GET /stats`.
6. Until backend is ready: mock server from API.md examples in `admin-ui/mocks/` (clearly labeled), switched by `NEXT_PUBLIC_API_URL`.

**Done when:** all buttons hit the real backend, no dead buttons, works on the demo dataset.
**Integration:** talks to backend only via HTTP. PR from `feat/ui-*`.

## Participant 4 — Infra / QA

**Goal:** honest numbers and one-command runs.

1. `Makefile`: `make up`, `make predict IN= OUT=`, `make eval`, `make test` — **H+6**.
2. `Dockerfile` + `docker-compose.yml` (service + admin-ui), `.env.example` without secrets.
3. `ml/labeling/` — LLM labeling of 3–5k train tickets by `taxonomy.json` criteria.
4. Gold set — hand-check ~200 tickets (category, priority), store locally in `data/` (not git).
5. `eval/eval.py` — category accuracy, priority macro-F1, invalid-citation rate, `needs_human` share, cost/ticket, p50/p95 latency.
6. `docs/REPORT.md` — metrics table, example drafts (ru/kk/uz), cost calc, limitations.

**Done when:** `make eval` prints a full metrics table on validation in one command.
**Integration:** reads `pred.jsonl` produced by `make predict`; does not touch service code. PR from `feat/infra-*`.

---

## Timeline

| Phase | Hours | Outcome |
|---|---|---|
| 0. Kickoff | H+0–2 | EDA, contract frozen, repo skeleton, LLM keys, questions to organizers |
| 1. Skeleton | H+2–6 | End-to-end chain on stubs; `make predict` valid; UI shows queue |
| 2. Core | H+6–20 | Labeling + classifier, RAG, validator, kk/uz; first honest `make eval` |
| 3. Boost | H+20–28 | Metric iterations, dedup, edits as training signal, cost/p95 measured |
| 4. Freeze | H+28–34 | No new features. Bugs, README, REPORT, demo script |
| 5. Submit | H+34–36 | Final run, pitch rehearsal |

## Git rules

1. `main` is always runnable. Work in `feat/<area>-<short>` branches, merge via PR; Tech Lead merges.
2. Contract changes only by Tech Lead, announced in team chat.
3. Metrics only from `make eval`.
4. Sync every 3–4 hours, 10 minutes: done / blocked / next.
5. **Never commit case data or `.env`.** Check `git status` before every commit.

## Open questions to organizers

1. Does `tickets_valid.jsonl` contain `category`/`priority` labels? (doc contradicts itself)
2. Is there a knowledge base beyond `rules.json`?
3. Kazakh drafts: proper Cyrillic or cp1251 look-alikes?
4. How is `make predict` run on the hidden set — our environment, do we leave an API key?
5. Are LLM credits provided?
