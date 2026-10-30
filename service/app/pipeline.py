"""Triage pipeline entry point. Owner: Tech Lead.

Backend calls `triage()`; the internals are replaced step by step.
Current state: STUB — returns a schema-valid "needs human" answer for every ticket.
"""
from __future__ import annotations

from contract.schemas import Extracted, TriageRequest, TriageResponse


def triage(req: TriageRequest) -> TriageResponse:
    # STUB: real steps (normalize → classify → dedup/guards → RAG → LLM → validate) come later.
    default_category = "complaint.other" if req.kind == "complaint" else "report.question"
    return TriageResponse(
        lang="und",
        category=default_category,
        priority="p3",
        is_spam_or_duplicate=False,
        extracted=Extracted(nickname=req.reported_nick),
        draft_reply=None,
        needs_human=True,
        confidence=0.0,
        reasons=["stub"],
    )
