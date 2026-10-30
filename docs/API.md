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

## Errors

| Code | When | Body |
|---|---|---|
| 400 | Malformed JSON | `{"error":"bad_request","detail":"..."}` |
| 404 | Unknown ticket id | `{"error":"not_found","detail":"..."}` |
| 422 | Valid JSON, schema mismatch | FastAPI validation body |
| 503 | LLM unavailable → service still answers `/triage` with `draft_reply=null`, `needs_human=true`, reason `llm_unavailable` | — |

`/triage` must never fail because of the LLM: it degrades to "needs human".

## Batch

```
make predict IN=tickets.jsonl OUT=pred.jsonl
```
One output line per input line, same order: `{"ticket_id": "...", ...TriageResponse}`.
