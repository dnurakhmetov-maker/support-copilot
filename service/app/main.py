"""FastAPI service. Owner: Backend.

Run from the repo root:  uvicorn service.app.main:app --port 8000

POST /triage   TriageRequest -> service.app.pipeline.triage() -> TriageResponse
GET  /health   liveness + whether the indexes are warm

/triage never returns 500 because of the pipeline: any exception degrades to a schema-valid
"needs human" answer (draft_reply=null, needs_human=true) with the reason in `reasons`.
"""
from __future__ import annotations

import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from contract.schemas import Extracted, TriageRequest, TriageResponse
from service.app import pipeline

VERSION = os.getenv("APP_VERSION", "0.1.0")
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",") if o.strip()]

log = logging.getLogger("support_copilot")

# Filled by the lifespan warm-up; exposed in /health so a cold or data-less service is visible.
STATE: dict = {"warm": False, "warmup_s": None, "warmup_error": None}


def warmup() -> None:
    """Build/load the cases and rules indexes once, so the first /triage doesn't wait ~10 s.

    Missing data must not stop the service from starting: /triage then degrades to "needs human".
    """
    from service.app.rag.cases_index import load_cases
    from service.app.rag.rules_index import load_rules

    t0 = time.time()
    try:
        load_rules()
        load_cases()
        STATE.update(warm=True, warmup_error=None)
    except Exception as e:  # missing data/, broken index cache, ...
        STATE.update(warm=False, warmup_error=f"{type(e).__name__}: {e}"[:300])
        log.warning("warm-up failed, /triage will degrade to needs_human: %s", STATE["warmup_error"])
    STATE["warmup_s"] = round(time.time() - t0, 3)


@asynccontextmanager
async def lifespan(_: FastAPI):
    if os.getenv("SKIP_WARMUP", "0") != "1":
        warmup()
    yield


app = FastAPI(title="Support Copilot", version=VERSION, lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])


# ---------- errors (docs/API.md "Errors") ----------
def _error(status: int, error: str, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": error, "detail": detail})


@app.exception_handler(RequestValidationError)
async def _validation_error(request: Request, exc: RequestValidationError):
    """Malformed JSON -> 400 with ErrorBody; valid JSON that doesn't match the schema -> default 422 body."""
    for err in exc.errors():
        if err.get("type") == "json_invalid":
            return _error(400, "bad_request", f"malformed JSON: {err.get('ctx', {}).get('error', 'cannot parse body')}")
    return await request_validation_exception_handler(request, exc)


@app.exception_handler(StarletteHTTPException)
async def _http_error(_: Request, exc: StarletteHTTPException):
    code = {400: "bad_request", 404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "error")
    return _error(exc.status_code, code, str(exc.detail))


# ---------- endpoints ----------
@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": VERSION, "warm": STATE["warm"], "warmup_error": STATE["warmup_error"]}


def _degraded(req: TriageRequest, reason: str) -> TriageResponse:
    """Schema-valid answer when the pipeline itself crashed: nothing is drafted, a human decides."""
    text = " ".join(m.text for m in req.messages if m.role == "player")
    try:
        lang = pipeline._detect_lang(text) if text.strip() else "und"
    except Exception:
        lang = "und"
    return TriageResponse(
        lang=lang if lang in pipeline.LANGS else "und",
        category="complaint.other" if req.kind == "complaint" else "report.question",
        priority="p2", is_spam_or_duplicate=False,
        extracted=Extracted(nickname=req.reported_nick),
        draft_reply=None, needs_human=True, confidence=0.0, reasons=[reason])


@app.post("/triage", response_model=TriageResponse)
def triage(req: TriageRequest) -> TriageResponse:
    # sync handler: FastAPI runs it in a worker thread, so the blocking LLM call doesn't stall the event loop
    try:
        return pipeline.triage(req)
    except Exception as e:
        log.exception("pipeline failed for ticket_id=%s", req.ticket_id)
        return _degraded(req, f"ошибка обработки ({type(e).__name__}) — требуется администратор")
