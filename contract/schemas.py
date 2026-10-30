"""Frozen API contract for Support Copilot.

Changes only by Tech Lead. Category values are validated against taxonomy.json at runtime
(list will be pinned here once data arrives).
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

Lang = Literal["ru", "kk", "uz", "en", "und"]
Priority = Literal["p0", "p1", "p2", "p3"]
Role = Literal["player", "reported_player", "admin"]
Kind = Literal["complaint", "report"]
Status = Literal["new", "sent", "escalated"]
Action = Literal["send", "edit", "escalate"]


# ---------- /triage ----------
class Message(BaseModel):
    role: Role
    text: str
    attachments: int = 0
    ts: Optional[str] = None


class TriageRequest(BaseModel):
    kind: Kind
    messages: list[Message] = Field(min_length=1)
    # optional fields available in batch files
    ticket_id: Optional[str] = None
    reporter_hash: Optional[str] = None
    reported_nick: Optional[str] = None
    rule_refs_raw: Optional[str] = None
    created_at: Optional[str] = None
    server_lang: Optional[str] = None
    region: Optional[str] = None


class Extracted(BaseModel):
    nickname: Optional[str] = None
    rule_refs: list[str] = []
    has_evidence: bool = False
    server: Optional[str] = None


class DraftReply(BaseModel):
    lang: Lang
    text: str
    cited: list[str] = []


class TriageResponse(BaseModel):
    lang: Lang
    category: str
    priority: Priority
    is_spam_or_duplicate: bool
    extracted: Extracted
    draft_reply: Optional[DraftReply]
    needs_human: bool
    confidence: float = Field(ge=0, le=1)
    reasons: list[str] = []


# ---------- admin API ----------
class HistoryItem(BaseModel):
    action: Action
    at: datetime
    final_text: Optional[str] = None
    comment: Optional[str] = None


class Ticket(BaseModel):
    id: str
    created_at: datetime
    status: Status
    request: TriageRequest
    triage: TriageResponse
    final_text: Optional[str] = None
    history: list[HistoryItem] = []


class ActionRequest(BaseModel):
    action: Action
    final_text: Optional[str] = None
    comment: Optional[str] = None


class ErrorBody(BaseModel):
    error: str
    detail: str
