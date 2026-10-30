"""Admin API tests: /tickets, /queue, /tickets/{id}, /tickets/{id}/action, /stats, /rules/{id}, /templates.

All texts are INVENTED. The SQLite DB lives in tmp_path (DB_PATH), never in data/.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from contract.schemas import DraftReply, Extracted, Ticket, TriageResponse
from service.app import main, pipeline, storage
from service.tests.test_api import COMPLAINT, REPORT, fake_data  # noqa: F401  (fixture re-export)


def _resp(category="report.question", priority="p3", needs_human=False, draft="Здравствуйте! Выдуманный ответ.") -> TriageResponse:
    return TriageResponse(lang="ru", category=category, priority=priority, is_spam_or_duplicate=False,
                          extracted=Extracted(), needs_human=needs_human, confidence=0.7,
                          draft_reply=DraftReply(lang="ru", text=draft, cited=[]) if draft else None)


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "db" / "test.sqlite"
    monkeypatch.setenv("DB_PATH", str(path))
    return path


@pytest.fixture
def client(db, monkeypatch):
    """pipeline.triage answers by a marker in the text, so each test controls category/priority."""
    monkeypatch.setenv("SKIP_WARMUP", "1")
    plan = {
        "P0": _resp("report.payment", "p0", True, None),
        "P1": _resp("complaint.insult", "p1", True),
        "P2": _resp("complaint.other", "p2", True),
        "P3": _resp("report.question", "p3", False),
    }

    def fake(req):
        text = req.messages[0].text
        if "CRASH" in text:
            raise RuntimeError("invented failure")
        return next((r for k, r in plan.items() if k in text), plan["P3"])

    monkeypatch.setattr(pipeline, "triage", fake)
    with TestClient(main.app) as c:
        yield c


def _post(c, marker: str, kind: str = "report", **extra) -> dict:
    r = c.post("/tickets", json={"kind": kind, "messages": [{"role": "player", "text": f"выдуманный тикет {marker}"}],
                                 **extra})
    assert r.status_code == 200, r.text
    return r.json()


# ---------- POST /tickets ----------
def test_create_ticket_stores_request_and_triage(client, db):
    body = {**COMPLAINT, "ticket_id": "t_inv_1", "reporter_hash": "h_inv", "images_count": 3, "region": "cis"}
    r = client.post("/tickets", json=body)
    assert r.status_code == 200
    t = Ticket.model_validate(r.json())
    assert t.id == "t_inv_1" and t.status == "new" and t.final_text is None and t.history == []
    assert t.request.images_count == 3 and t.request.reporter_hash == "h_inv" and t.request.region == "cis"
    assert t.triage.category == "report.question"
    assert db.exists()  # DB_PATH respected, parent dir created


def test_create_ticket_generates_id_when_missing(client):
    a, b = _post(client, "P3"), _post(client, "P3")
    assert a["id"].startswith("t_") and a["id"] != b["id"]


def test_create_ticket_is_idempotent_by_ticket_id(client, monkeypatch):
    first = _post(client, "P1", ticket_id="t_dup")
    monkeypatch.setattr(pipeline, "triage", lambda req: pytest.fail("must not re-triage a stored ticket"))
    again = _post(client, "P3", ticket_id="t_dup")
    assert again == first
    assert client.get("/stats").json()["total"] == 1


def test_create_ticket_pipeline_crash_still_stored_as_needs_human(client):
    t = _post(client, "CRASH", kind="complaint")
    assert t["triage"]["needs_human"] is True and t["triage"]["draft_reply"] is None
    assert "RuntimeError" in t["triage"]["reasons"][0]
    assert client.get(f"/tickets/{t['id']}").status_code == 200


def test_create_ticket_validation(client):
    assert client.post("/tickets", json={"kind": "report", "messages": []}).status_code == 422
    r = client.post("/tickets", content=b"{oops", headers={"Content-Type": "application/json"})
    assert r.status_code == 400 and r.json()["error"] == "bad_request"


# ---------- GET /queue ----------
def test_queue_sorted_by_priority_then_oldest(client):
    ids = {m: _post(client, m, ticket_id=f"t_{m.lower()}_{i}")["id"] for i, m in enumerate(["P3", "P1", "P0", "P2"])}
    older_p1 = ids["P1"]
    newer_p1 = _post(client, "P1", ticket_id="t_p1_late")["id"]
    q = client.get("/queue").json()
    assert isinstance(q, list)  # plain array, no wrapper
    assert [t["id"] for t in q] == [ids["P0"], older_p1, newer_p1, ids["P2"], ids["P3"]]


def test_queue_filters_and_limit(client):
    for m in ["P0", "P1", "P1", "P3"]:
        _post(client, m)
    assert [t["triage"]["priority"] for t in client.get("/queue", params={"priority": "p1"}).json()] == ["p1", "p1"]
    assert len(client.get("/queue", params={"limit": 2}).json()) == 2
    sent = client.get("/queue").json()[-1]["id"]
    client.post(f"/tickets/{sent}/action", json={"action": "send"})
    new = client.get("/queue", params={"status": "new"}).json()
    assert len(new) == 3 and sent not in {t["id"] for t in new}
    assert [t["id"] for t in client.get("/queue", params={"status": "sent"}).json()] == [sent]


@pytest.mark.parametrize("params", [{"status": "open"}, {"priority": "p9"}, {"limit": 0}, {"limit": 501}])
def test_queue_bad_params_422(client, params):
    assert client.get("/queue", params=params).status_code == 422


# ---------- GET /tickets/{id} ----------
def test_unknown_ticket_404(client):
    for r in (client.get("/tickets/t_nope"), client.post("/tickets/t_nope/action", json={"action": "send"})):
        assert r.status_code == 404
        assert r.json()["error"] == "not_found" and "t_nope" in r.json()["detail"]


# ---------- POST /tickets/{id}/action + history ----------
def test_send_uses_draft_when_no_text(client):
    t = _post(client, "P3")
    r = client.post(f"/tickets/{t['id']}/action", json={"action": "send"})
    assert r.status_code == 200
    out = r.json()
    assert out["status"] == "sent" and out["final_text"] == "Здравствуйте! Выдуманный ответ."
    assert [h["action"] for h in out["history"]] == ["send"]
    assert out["history"][0]["final_text"] == out["final_text"]


def test_edit_then_escalate_full_history(client):
    t = _post(client, "P2", kind="complaint")
    e = client.post(f"/tickets/{t['id']}/action",
                    json={"action": "edit", "final_text": "Здравствуйте! Исправленный выдуманный ответ.", "comment": "тон"})
    assert e.status_code == 200 and e.json()["status"] == "sent"
    x = client.post(f"/tickets/{t['id']}/action", json={"action": "escalate", "comment": "нужен старший админ"})
    assert x.status_code == 200
    got = client.get(f"/tickets/{t['id']}").json()
    assert got["status"] == "escalated"
    assert got["final_text"] == "Здравствуйте! Исправленный выдуманный ответ."  # escalate without text keeps it
    h = got["history"]
    assert [i["action"] for i in h] == ["edit", "escalate"]
    assert h[0]["final_text"] == "Здравствуйте! Исправленный выдуманный ответ." and h[0]["comment"] == "тон"
    assert h[1]["final_text"] is None and h[1]["comment"] == "нужен старший админ"
    assert datetime.fromisoformat(h[0]["at"]) <= datetime.fromisoformat(h[1]["at"])
    assert got["triage"]["draft_reply"]["text"] == "Здравствуйте! Выдуманный ответ."  # original draft kept for training


def test_edit_requires_text(client):
    t = _post(client, "P3")
    for body in ({"action": "edit"}, {"action": "edit", "final_text": "   "}):
        r = client.post(f"/tickets/{t['id']}/action", json=body)
        assert r.status_code == 400 and r.json()["error"] == "bad_request"
    assert client.get(f"/tickets/{t['id']}").json()["history"] == []


def test_send_without_draft_requires_text(client):
    t = _post(client, "P0")  # payment: no draft
    assert client.post(f"/tickets/{t['id']}/action", json={"action": "send"}).status_code == 400
    r = client.post(f"/tickets/{t['id']}/action", json={"action": "send", "final_text": "Выдуманный ответ админа."})
    assert r.status_code == 200 and r.json()["final_text"] == "Выдуманный ответ админа."


def test_bad_action_422(client):
    t = _post(client, "P3")
    assert client.post(f"/tickets/{t['id']}/action", json={"action": "delete"}).status_code == 422


def test_history_persisted_in_sqlite(client, db):
    t = _post(client, "P3")
    client.post(f"/tickets/{t['id']}/action", json={"action": "edit", "final_text": "Выдуманный текст."})
    with sqlite3.connect(db) as conn:
        rows = conn.execute("SELECT action, final_text FROM history WHERE ticket_id = ?", (t["id"],)).fetchall()
    assert rows == [("edit", "Выдуманный текст.")]


# ---------- GET /stats ----------
def test_stats_empty(client):
    assert client.get("/stats").json() == {"total": 0, "by_category": {}, "by_priority": {}, "by_status": {},
                                           "needs_human_share": 0.0, "avg_latency_ms": 0}


def test_stats_counts(client):
    for m in ["P0", "P1", "P2", "P3"]:
        _post(client, m)
    tid = client.get("/queue").json()[0]["id"]
    client.post(f"/tickets/{tid}/action", json={"action": "escalate"})
    s = client.get("/stats").json()
    assert s["total"] == 4
    assert s["by_priority"] == {"p0": 1, "p1": 1, "p2": 1, "p3": 1}
    assert s["by_category"] == {"complaint.insult": 1, "complaint.other": 1, "report.payment": 1, "report.question": 1}
    assert s["by_status"] == {"escalated": 1, "new": 3}
    assert s["needs_human_share"] == 0.75
    assert isinstance(s["avg_latency_ms"], int) and s["avg_latency_ms"] >= 0


# ---------- storage survives a restart ----------
def test_data_survives_new_connection(client):
    t = _post(client, "P1", ticket_id="t_persist")
    assert storage.get_ticket("t_persist").triage == TriageResponse.model_validate(t["triage"])


# ---------- end-to-end: real pipeline on invented data, LLM disabled ----------
def test_e2e_tickets_real_pipeline_llm_none(fake_data, db, monkeypatch):  # noqa: F811
    monkeypatch.setenv("LLM_PROVIDER", "none")
    with TestClient(main.app) as c:
        t = c.post("/tickets", json={**REPORT, "images_count": 1}).json()
        assert t["triage"]["needs_human"] is True and t["triage"]["draft_reply"] is None
        assert t["request"]["images_count"] == 1
        # no draft -> admin writes the answer himself; it lands in history
        r = c.post(f"/tickets/{t['id']}/action", json={"action": "send", "final_text": "Выдуманный ответ."})
        assert r.status_code == 200 and r.json()["history"][0]["final_text"] == "Выдуманный ответ."
        assert c.get("/stats").json()["by_status"] == {"sent": 1}


def test_rules_endpoint(fake_data, monkeypatch):  # noqa: F811
    monkeypatch.setenv("SKIP_WARMUP", "1")
    with TestClient(main.app) as c:
        assert c.get("/rules/9.1").json() == {"id": "9.1", "section": "Invented general",
                                              "text": "invented: no insults in chat"}
        assert c.get("/rules/ЖБ%208.1.1").json()["id"] == "ЖБ 8.1.1"
        r = c.get("/rules/77.7")
        assert r.status_code == 404 and r.json()["error"] == "not_found"


def test_rules_endpoint_without_data(tmp_path, monkeypatch):
    from service.app.rag import rules_index

    monkeypatch.setenv("SKIP_WARMUP", "1")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "missing"))
    rules_index.load_rules.cache_clear()
    try:
        with TestClient(main.app) as c:
            r = c.get("/rules/9.1")
        assert r.status_code == 404 and r.json()["error"] == "not_found"
    finally:
        rules_index.load_rules.cache_clear()


# ---------- training signal: model draft + admin's final text + history, end-to-end ----------
def test_e2e_training_signal_draft_vs_final(fake_data, db, monkeypatch):  # noqa: F811
    """Real pipeline with a fake LLM reply: the stored ticket keeps the model's draft next to what the admin sent."""
    from service.app.llm.provider import LLMResult

    draft = "Здравствуйте! Как поменять цвет машины, смотрите в меню выдуманного гаража."
    reply = {"lang": "ru", "category": "report.question", "priority": "p3", "is_spam_or_duplicate": False,
             "extracted": {"nickname": None, "rule_refs": [], "has_evidence": False, "server": None},
             "draft_text": draft, "cited": [], "needs_human": False, "confidence": 0.9, "reasons": []}
    monkeypatch.setattr(pipeline, "complete_json",
                        lambda *a, **kw: LLMResult(json.dumps(reply, ensure_ascii=False), 100, 50, 0.01, "fake"))
    final = "Здравствуйте! Цвет меняется в меню гаража, кнопка «Покраска». Выдуманный текст админа."
    with TestClient(main.app) as c:
        t = c.post("/tickets", json={**REPORT, "ticket_id": "t_signal"}).json()
        model_draft = t["triage"]["draft_reply"]["text"]
        assert model_draft.startswith("Здравствуйте!")
        c.post("/tickets/t_signal/action", json={"action": "edit", "final_text": final, "comment": "уточнил кнопку"})
        got = c.get("/tickets/t_signal").json()
    assert got["triage"]["draft_reply"]["text"] == model_draft          # model's draft is untouched
    assert got["final_text"] == final and got["status"] == "sent"       # what the admin actually sent
    assert got["history"] == [{"action": "edit", "at": got["history"][0]["at"], "final_text": final,
                               "comment": "уточнил кнопку"}]


# ---------- GET /templates ----------
def test_templates_served_from_data_dir(tmp_path, monkeypatch):
    invented = [{"kind": "report", "size": 120, "template": "Здравствуйте! Выдуманный шаблон {nick}.",
                 "distinct_replies_share": 0.4}]
    (tmp_path / "templates").mkdir()
    (tmp_path / "templates" / "proposals.json").write_text(json.dumps(invented, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SKIP_WARMUP", "1")
    with TestClient(main.app) as c:
        r = c.get("/templates")
    assert r.status_code == 200 and r.json() == invented


def test_templates_missing_or_broken_404(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SKIP_WARMUP", "1")
    with TestClient(main.app) as c:
        r = c.get("/templates")
        assert r.status_code == 404 and r.json()["error"] == "not_found"
        assert "python -m service.app.templates_mining" in r.json()["detail"]
        (tmp_path / "templates").mkdir()
        (tmp_path / "templates" / "proposals.json").write_text("{broken", encoding="utf-8")
        r = c.get("/templates")
        assert r.status_code == 404 and "unreadable" in r.json()["detail"]
