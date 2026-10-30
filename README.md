# Support Copilot — Decentrathon 6.0 × Grand Mobile (Case 03)

Triage service for player complaints and in-game reports: detects language, category and
priority, filters spam/duplicates, extracts facts, drafts a reply in the player's language
with citations to game rules, and decides honestly when a human admin is needed.

> **Status:** project skeleton. Architecture and API contract are frozen; modules are in progress.

## Documents

| Doc | What's inside |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Pipeline, modules, design decisions |
| [docs/API.md](docs/API.md) | Frozen API contract (`POST /triage` + admin endpoints) |
| [docs/TEAM.md](docs/TEAM.md) | Who owns what, task cards, timeline, git rules |

## Repository layout

```
contract/        API contract (pydantic schemas)      — owner: Tech Lead
service/app/     FastAPI service + pipeline           — owners: see docs/TEAM.md
ml/              labeling + classifier training       — Infra/QA + Tech Lead
eval/            metrics, cost, latency               — Infra/QA
admin-ui/        Next.js admin interface              — Frontend
docs/            architecture, API, team plan, report
data/            LOCAL ONLY, git-ignored — never commit case data
```

## Data policy (mandatory)

Case data is provided by Grand Mobile under NDA-like terms: use only for this hackathon,
**never commit it to git**, delete within 14 days after results. `data/` is in `.gitignore`.

## Quick start

Needs Python 3.12, GNU make and (for `make up`) Docker. Run everything from the repo root.

1. Virtualenv — create it once, then activate it in every new terminal:

   | | macOS / Linux | Windows (PowerShell) |
   |---|---|---|
   | create | `python3 -m venv .venv` | `py -3.12 -m venv .venv` |
   | activate | `source .venv/bin/activate` | `.venv\Scripts\activate` |

   With [uv](https://docs.astral.sh/uv/): `uv venv --python 3.12 --seed .venv` (`--seed` adds pip, which `make install` uses).
   Windows: `winget install --id ezwinports.make -e` gives you `make`.

2. Install deps, unpack the case data (archive from the Tech Lead, never committed), set the LLM key:
   ```
   make install
   make data ARCHIVE=~/Downloads/handout.gz
   ```
   Copy `.env.example` to `.env` and put your key into `LLM_API_KEY` (`.env` is git-ignored).

3. Run:

   | Command | What it does |
   |---|---|
   | `make run` | API on http://localhost:8000 |
   | `make predict IN=data/tickets_valid.jsonl OUT=data/runs/pred.jsonl` | batch triage → `pred.jsonl` + `pred.meta.jsonl` (latency, tokens, cost) |
   | `make predict ... LIMIT=50 SLEEP=4 RESUME=1` | first 50 tickets, 4 s pause per LLM call (free tier ≈ 15 req/min), reuse answers of an interrupted run |
   | `make predict ... LLM_PROVIDER=none` | whole chain without the LLM (every ticket → "needs human"), costs nothing |
   | `make eval` | metrics table on `data/gold.jsonl` |
   | `make test` | pytest |
   | `make up` / `make down` | docker compose: service on :8000 (+ admin-ui on :3000 once it has a Dockerfile); `data/` is mounted, `.env` passed in |
