"""SQLite storage for the admin API. Owner: Backend.

DB file: env DB_PATH (default data/app.sqlite, git-ignored). One connection per call — the API runs sync
handlers in a thread pool, and sqlite3 connections must not be shared across threads.

Every admin action (send / edit / escalate + final text + comment) is appended to `history`:
draft vs. what the admin actually sent is the training signal for later prompt/model tuning.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from contract.schemas import HistoryItem, Ticket, TriageRequest, TriageResponse

PRIORITY_ORDER = "CASE priority WHEN 'p0' THEN 0 WHEN 'p1' THEN 1 WHEN 'p2' THEN 2 ELSE 3 END"
# action -> resulting ticket status. "edit" = the admin corrected the draft and sent it.
ACTION_STATUS = {"send": "sent", "edit": "sent", "escalate": "escalated"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets (
    id          TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL,
    status      TEXT NOT NULL,
    kind        TEXT NOT NULL,
    category    TEXT NOT NULL,
    priority    TEXT NOT NULL,
    needs_human INTEGER NOT NULL,
    latency_ms  REAL,
    request     TEXT NOT NULL,
    triage      TEXT NOT NULL,
    final_text  TEXT
);
CREATE INDEX IF NOT EXISTS ix_tickets_queue ON tickets (status, priority, created_at);
CREATE TABLE IF NOT EXISTS history (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id  TEXT NOT NULL REFERENCES tickets(id),
    action     TEXT NOT NULL,
    at         TEXT NOT NULL,
    final_text TEXT,
    comment    TEXT
);
CREATE INDEX IF NOT EXISTS ix_history_ticket ON history (ticket_id, seq);
"""


def db_path() -> str:
    return os.getenv("DB_PATH", os.path.join(os.getenv("DATA_DIR", "data"), "app.sqlite"))


@contextmanager
def connect():
    path = db_path()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(_SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _history(conn: sqlite3.Connection, ticket_id: str) -> list[HistoryItem]:
    rows = conn.execute("SELECT action, at, final_text, comment FROM history WHERE ticket_id = ? ORDER BY seq",
                        (ticket_id,)).fetchall()
    return [HistoryItem(action=r["action"], at=r["at"], final_text=r["final_text"], comment=r["comment"]) for r in rows]


def _ticket(conn: sqlite3.Connection, row: sqlite3.Row) -> Ticket:
    return Ticket(id=row["id"], created_at=row["created_at"], status=row["status"],
                  request=TriageRequest.model_validate_json(row["request"]),
                  triage=TriageResponse.model_validate_json(row["triage"]),
                  final_text=row["final_text"], history=_history(conn, row["id"]))


def get_ticket(ticket_id: str) -> Ticket | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        return _ticket(conn, row) if row else None


def insert_ticket(ticket_id: str, req: TriageRequest, triage: TriageResponse, latency_ms: float | None) -> Ticket:
    with connect() as conn:
        conn.execute(
            "INSERT INTO tickets (id, created_at, status, kind, category, priority, needs_human, latency_ms,"
            " request, triage) VALUES (?, ?, 'new', ?, ?, ?, ?, ?, ?, ?)",
            (ticket_id, _now().isoformat(), req.kind, triage.category, triage.priority, int(triage.needs_human),
             latency_ms, req.model_dump_json(), triage.model_dump_json()))
        return _ticket(conn, conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone())


def list_queue(status: str | None = None, priority: str | None = None, limit: int = 50) -> list[Ticket]:
    """Sorted p0 -> p3, then oldest first."""
    where, args = [], []
    if status:
        where.append("status = ?")
        args.append(status)
    if priority:
        where.append("priority = ?")
        args.append(priority)
    sql = "SELECT * FROM tickets" + (" WHERE " + " AND ".join(where) if where else "")
    sql += f" ORDER BY {PRIORITY_ORDER}, created_at, id LIMIT ?"
    with connect() as conn:
        return [_ticket(conn, r) for r in conn.execute(sql, (*args, limit)).fetchall()]


def add_action(ticket_id: str, action: str, final_text: str | None, comment: str | None) -> Ticket | None:
    """Append to history and move the ticket to the action's status. None if the ticket doesn't exist."""
    with connect() as conn:
        if not conn.execute("SELECT 1 FROM tickets WHERE id = ?", (ticket_id,)).fetchone():
            return None
        conn.execute("INSERT INTO history (ticket_id, action, at, final_text, comment) VALUES (?, ?, ?, ?, ?)",
                     (ticket_id, action, _now().isoformat(), final_text, comment))
        conn.execute("UPDATE tickets SET status = ?, final_text = COALESCE(?, final_text) WHERE id = ?",
                     (ACTION_STATUS[action], final_text, ticket_id))
        return _ticket(conn, conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone())


def stats() -> dict:
    with connect() as conn:
        def counts(col: str) -> dict:
            return {r[0]: r[1] for r in conn.execute(f"SELECT {col}, COUNT(*) FROM tickets GROUP BY {col} ORDER BY {col}")}

        total, nh, lat = conn.execute("SELECT COUNT(*), SUM(needs_human), AVG(latency_ms) FROM tickets").fetchone()
        return {"total": total, "by_category": counts("category"), "by_priority": counts("priority"),
                "by_status": counts("status"), "needs_human_share": round((nh or 0) / total, 3) if total else 0.0,
                "avg_latency_ms": round(lat) if lat is not None else 0}

