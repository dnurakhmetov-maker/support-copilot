"""API tests. All texts, rules and taxonomy here are INVENTED (never use case data in tests).

Two layers:
  * contract tests with pipeline.triage monkeypatched — error table, CORS, crash degradation;
  * end-to-end tests through the real pipeline on an invented mini data dir (built in tmp_path),
    with LLM_PROVIDER=none and with a fake LLM reply.
"""
from __future__ import annotations

import gzip
import json

import pytest
from fastapi.testclient import TestClient

from contract.schemas import DraftReply, Extracted, TriageResponse
from service.app import main, pipeline
from service.app.llm import prompt
from service.app.rag import cases_index, rules_index

COMPLAINT = {
    "kind": "complaint",
    "messages": [{"role": "player", "text": "Zorro_Testov обзывал меня в чате на спавне, есть видео <url>, 9.1 ОП",
                  "attachments": 2}],
}
REPORT = {"kind": "report", "messages": [{"role": "player", "text": "как поменять цвет машины в тестовом гараже?"}]}

FAKE_RULES = {"rules": [
    {"id": 30, "category": "Invented general", "points": [{"ref": "9.1", "text": "invented: no insults in chat"},
                                                         {"ref": "9.2", "text": "invented: no spam"}]},
    {"id": 31, "category": "Invented complaints", "points": [{"ref": "8.1.1", "text": "invented: attach proof"}]},
]}
FAKE_TAXONOMY = {
    "categories": [
        {"code": "complaint.insult", "kind": "complaint", "title": "invented insult", "rules": ["9.1"]},
        {"code": "complaint.other", "kind": "complaint", "title": "invented other"},
        {"code": "complaint.invalid", "kind": "complaint", "title": "invented invalid", "rules": ["8.1.1 ЖБ"]},
        {"code": "report.question", "kind": "report", "title": "invented question"},
        {"code": "report.payment", "kind": "report", "title": "invented payment"},
    ],
    "priorities": [{"code": f"p{i}", "title": f"invented p{i}", "rule": "invented"} for i in range(4)],
    "needs_human_always": ["report.payment"],
}
FAKE_TRAIN = [
    ("c1", "complaint", "игрок обзывал меня в чате на спавне", "Здравствуйте! Нарушение 9.1 ОП подтверждено."),
    ("c2", "complaint", "игрок оскорблял в чате у банка", "Здравствуйте! Жалоба рассмотрена, пункт 9.1 ОП."),
    ("c3", "complaint", "игрок спамил в чате весь вечер", "Здравствуйте! Нарушение пункта 9.2 ОП."),
    ("c4", "complaint", "нет доказательств, просто наказать игрока", "Здравствуйте! Нужны доказательства, 8.1.1 ЖБ."),
    ("r1", "report", "как поменять цвет машины в гараже", "Здравствуйте! Цвет меняется в меню гаража."),
    ("r2", "report", "как поменять номер машины в гараже", "Здравствуйте! Номер меняется в меню гаража."),
    ("r3", "report", "где найти меню гаража для машины", "Здравствуйте! Меню открывается клавишей в гараже."),
]


def _response(**kw) -> TriageResponse:
    base = dict(lang="ru", category="complaint.insult", priority="p2", is_spam_or_duplicate=False,
                extracted=Extracted(nickname="Zorro_Testov", rule_refs=["9.1"], has_evidence=True),
                draft_reply=DraftReply(lang="ru", text="Здравствуйте! Жалоба передана администратору.", cited=["9.1"]),
                needs_human=True, confidence=0.8, reasons=["invented reason"])
    return TriageResponse(**{**base, **kw})


@pytest.fixture
def client(monkeypatch):
    """Client without warm-up (no data needed); pipeline.triage is replaced per test."""
    monkeypatch.setenv("SKIP_WARMUP", "1")
    with TestClient(main.app) as c:
        yield c


def _clear_caches():
    cases_index.load_cases.cache_clear()
    rules_index.load_rules.cache_clear()
    prompt.load_taxonomy.cache_clear()


@pytest.fixture
def fake_data(tmp_path, monkeypatch):
    """Invented mini data dir; real pipeline and real warm-up run against it."""
    (tmp_path / "rules.json").write_text(json.dumps(FAKE_RULES, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "taxonomy.json").write_text(json.dumps(FAKE_TAXONOMY, ensure_ascii=False), encoding="utf-8")
    with gzip.open(tmp_path / "tickets_train.jsonl.gz", "wt", encoding="utf-8") as f:
        for tid, kind, player, admin in FAKE_TRAIN:
            f.write(json.dumps({"ticket_id": tid, "kind": kind, "lang": "ru", "messages": [
                {"role": "player", "text": player}, {"role": "admin", "text": admin}]}, ensure_ascii=False) + "\n")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(cases_index, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(cases_index, "INDEX_DIR", str(tmp_path / "index"))
    monkeypatch.delenv("SKIP_WARMUP", raising=False)
    _clear_caches()
    yield tmp_path
    _clear_caches()


# ---------- contract / error table ----------
def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["version"]


def test_triage_returns_pipeline_response(client, monkeypatch):
    seen = {}

    def fake(req):
        seen["req"] = req
        return _response()

    monkeypatch.setattr(pipeline, "triage", fake)
    r = client.post("/triage", json={**COMPLAINT, "ticket_id": "t_test_1", "region": "cis"})
    assert r.status_code == 200
    out = TriageResponse.model_validate(r.json())
    assert out.category == "complaint.insult" and out.draft_reply.cited == ["9.1"]
    assert seen["req"].ticket_id == "t_test_1" and seen["req"].region == "cis"  # optional fields passed through


def test_malformed_json_is_400(client):
    r = client.post("/triage", content=b'{"kind": "complaint", "messages": [', headers={"Content-Type": "application/json"})
    assert r.status_code == 400
    assert r.json()["error"] == "bad_request" and r.json()["detail"]


@pytest.mark.parametrize("body", [
    {"kind": "complaint", "messages": []},                                     # min_length=1
    {"kind": "appeal", "messages": [{"role": "player", "text": "x"}]},         # unknown kind
    {"kind": "report", "messages": [{"role": "moderator", "text": "x"}]},      # unknown role
    {"messages": [{"role": "player", "text": "x"}]},                           # missing kind
    [1, 2, 3],                                                                 # valid JSON, wrong shape
])
def test_schema_mismatch_is_422(client, body):
    r = client.post("/triage", json=body)
    assert r.status_code == 422
    assert "detail" in r.json() and isinstance(r.json()["detail"], list)  # FastAPI validation body


def test_unknown_route_is_404_error_body(client):
    r = client.get("/no-such-endpoint")
    assert r.status_code == 404
    assert r.json()["error"] == "not_found"


@pytest.mark.parametrize("payload", [COMPLAINT, REPORT])
def test_pipeline_crash_degrades_to_needs_human(client, monkeypatch, payload):
    def boom(req):
        raise RuntimeError("invented failure")

    monkeypatch.setattr(pipeline, "triage", boom)
    r = client.post("/triage", json={**payload, "reported_nick": "Zorro_Testov"})
    assert r.status_code == 200
    out = TriageResponse.model_validate(r.json())
    assert out.needs_human is True and out.draft_reply is None and out.confidence == 0.0
    assert out.reasons and "RuntimeError" in out.reasons[0]
    assert out.category.startswith(payload["kind"] + ".")
    assert out.extracted.nickname == "Zorro_Testov"


def test_cors_preflight_localhost_3000(client):
    r = client.options("/triage", headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST",
                                           "Access-Control-Request-Headers": "content-type"})
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_cors_rejects_other_origin(client):
    r = client.get("/health", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in r.headers


def test_warmup_without_data_does_not_crash(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "missing"))
    monkeypatch.setattr(cases_index, "DATA_DIR", str(tmp_path / "missing"))
    monkeypatch.setattr(cases_index, "INDEX_DIR", str(tmp_path / "missing" / "index"))
    monkeypatch.delenv("SKIP_WARMUP", raising=False)
    _clear_caches()
    try:
        with TestClient(main.app) as c:
            h = c.get("/health").json()
            assert h["status"] == "ok" and h["warm"] is False and h["warmup_error"]
            r = c.post("/triage", json=COMPLAINT)  # data missing -> pipeline raises -> degraded answer, not 500
            assert r.status_code == 200 and r.json()["needs_human"] is True
    finally:
        _clear_caches()


# ---------- end-to-end through the real pipeline (invented data) ----------
def test_e2e_llm_disabled_needs_human(fake_data, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "none")
    with TestClient(main.app) as c:
        h = c.get("/health").json()
        assert h["warm"] is True, h
        assert (fake_data / "index" / "cases.pkl").exists()  # warm-up built the index on startup
        for payload in (COMPLAINT, REPORT):
            r = c.post("/triage", json=payload)
            assert r.status_code == 200
            out = TriageResponse.model_validate(r.json())
            assert out.needs_human is True and out.draft_reply is None
            assert out.lang == "ru" and out.reasons


def test_e2e_fake_llm_reply_is_validated(fake_data, monkeypatch):
    """A fake LLM answer goes through the real validator: invented rule ids are dropped from `cited`."""
    from service.app.llm.provider import LLMResult

    reply = {"lang": "ru", "category": "complaint.insult", "priority": "p2", "is_spam_or_duplicate": False,
             "extracted": {"nickname": "Zorro_Testov", "rule_refs": ["9.1 ОП"], "has_evidence": True, "server": None},
             "draft_text": "Здравствуйте! Жалоба на игрока передана администратору для проверки видео.",
             "cited": ["9.1", "77.7"], "needs_human": False, "confidence": 0.9, "reasons": []}
    monkeypatch.setattr(pipeline, "complete_json",
                        lambda *a, **kw: LLMResult(json.dumps(reply, ensure_ascii=False), 100, 50, 0.01, "fake"))
    with TestClient(main.app) as c:
        r = c.post("/triage", json=COMPLAINT)
    assert r.status_code == 200
    out = TriageResponse.model_validate(r.json())
    assert out.category == "complaint.insult"
    assert out.draft_reply is not None and out.draft_reply.cited == ["9.1"]  # 77.7 doesn't exist -> dropped
    assert out.extracted.rule_refs == ["9.1"]
    assert out.needs_human is True  # complaints always go to a human (pipeline rule)
