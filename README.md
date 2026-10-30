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

_Will be filled in once the walking skeleton is ready:_ `make up`, `make predict IN=... OUT=...`, `make eval`.
