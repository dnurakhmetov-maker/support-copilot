# API contract (frozen)

Source of truth: [`contract/schemas.py`](../contract/schemas.py). Changes only by Tech Lead,
announced to the whole team.

Base URL (local): `http://localhost:8000`

## POST /triage — official contract from the case

Request:
```json
{
  "kind": "complaint",
  "messages": [
    { "role": "player", "text": "Arman_Petrov оскорблял меня и мою семью в чате, скрины есть, 2.1 ОП", "attachments": 2 }
  ]
}
```
`kind`: `complaint` | `report`. `role`: `player` | `reported_player` | `admin`.
Optional request fields (present in batch files, all optional over HTTP): `ticket_id`, `reporter_hash`, `reported_nick`,
`rule_refs_raw`, `created_at`, `server_lang`, `region`, `images_count`. Batch tools must pass them through as-is.

Response `200`:
```json
{
  "lang": "ru",
  "category": "complaint.insult",
  "priority": "p2",
  "is_spam_or_duplicate": false,
  "extracted": { "nickname": "Arman_Petrov", "rule_refs": ["2.1"], "has_evidence": true, "server": null },
  "draft_reply": { "lang": "ru", "text": "Здравствуйте! ...", "cited": ["2.1"] },
  "needs_human": true,
  "confidence": 0.82,
  "reasons": ["решение о наказании подтверждает администратор"]
}
```

Guarantees:
- every id in `draft_reply.cited` exists in `rules.json`;
- `needs_human=true` for payments, ban appeals, account hacking, unban/refund/punishment topics;
- `category` values come only from `taxonomy.json` (exact list added after data arrives).

## Admin endpoints (our extension, for admin-ui)

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | `{"status":"ok","version":"..."}` |
| POST | `/tickets` | Ingest ticket (same body as `/triage` + optional `ticket_id`), run triage, store. Returns `Ticket` |
| GET | `/queue?status=new&priority=p0&limit=50` | List tickets sorted by priority, then time |
| GET | `/tickets/{id}` | Full ticket: messages + triage result + history |
| POST | `/tickets/{id}/action` | `{"action":"send"|"edit"|"escalate","final_text":"...","comment":"..."}` |
| GET | `/stats` | Counts by category/priority/status, share of `needs_human`, avg latency |
| GET | `/rules/{id}` | Rule text for a canonical citation id (e.g. `2.1`, `GZ 1.2`, URL-encode the space) — for hover tooltips |

`Ticket` object:
```json
{
  "id": "t_123", "created_at": "2026-10-30T12:00:00Z",
  "status": "new",
  "request": { "...TriageRequest" },
  "triage": { "...TriageResponse" },
  "final_text": null,
  "history": [ { "action": "edit", "at": "...", "final_text": "..." } ]
}
```
`status`: `new` | `sent` | `escalated`.

`GET /queue` returns a **plain JSON array** `Ticket[]` (no wrapper), sorted p0→p3, then oldest first.

`GET /stats`:
```json
{"total": 120, "by_category": {"complaint.insult": 40}, "by_priority": {"p0": 2, "p1": 10, "p2": 60, "p3": 48},
 "by_status": {"new": 90, "sent": 25, "escalated": 5}, "needs_human_share": 0.58, "avg_latency_ms": 2100}
```

`GET /rules/{id}` → `{"id": "2.1", "section": "<section title>", "text": "<rule text>"}`; 404 if the id does not exist.
Served from `data/rules.json` at runtime via `service.app.rag.rules_index.load_rules()` — rule texts are never copied
into the repo or the UI code.

Category codes (`complaint.insult`, `report.help`, …) are identifiers and may be used in UI code/mocks; human-readable
labels in the UI are our own wording.

## Errors

| Code | When | Body |
|---|---|---|
| 400 | Malformed JSON | `{"error":"bad_request","detail":"..."}` |
| 404 | Unknown ticket id | `{"error":"not_found","detail":"..."}` |
| 422 | Valid JSON, schema mismatch | FastAPI validation body |

`/triage` never fails because of the LLM or the pipeline: it returns **200** with a schema-valid answer,
`draft_reply=null`, `needs_human=true` and the reason in `reasons` (e.g. «LLM недоступен — требуется администратор»).
There is no 503.

## Duplicates

`is_spam_or_duplicate=true` is also set by a deterministic dedup step: same `reporter_hash`, similar text, within
60 minutes of a previous ticket (by `created_at`, or arrival time if absent). Implemented in `service/app/dedup.py`
(Backend), called from the pipeline, so both the API and `make predict` get it.

## Batch

```
make predict IN=tickets.jsonl OUT=pred.jsonl
```
One output line per input line, same order: `{"ticket_id": "...", ...TriageResponse}`.
