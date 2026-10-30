"""Tests on an INVENTED mini rulebook (never use case data in tests)."""
from service.app.rag.rules_index import RulesIndex
from service.app.validate import validate_draft

FAKE_RULES = {
    "rules": [
        {"id": 30, "category": "General", "points": [{"ref": "2.1", "text": "invented a"}, {"ref": "5.2", "text": "invented b"}]},
        {"id": 31, "category": "Complaints", "points": [{"ref": "1.2.6", "text": "invented c"}]},
        {"id": 32, "category": "Green zone", "points": [{"ref": "1.2", "text": "invented d"}]},
    ]
}
IDX = RulesIndex(FAKE_RULES)


def test_canonical_ids():
    assert set(IDX.points) == {"2.1", "5.2", "ЖБ 1.2.6", "GZ 1.2"}


def test_resolve_aliases():
    assert IDX.resolve("2.1 ОП") == "2.1"
    assert IDX.resolve("2.1оп") == "2.1"
    assert IDX.resolve("2.1 OP") == "2.1"
    assert IDX.resolve("1.2 GZ") == "GZ 1.2"
    assert IDX.resolve("gz 1.2") == "GZ 1.2"
    assert IDX.resolve("1.2.6 ППЖ") == "ЖБ 1.2.6"
    assert IDX.resolve("ЖБ 1.2.6") == "ЖБ 1.2.6"
    assert IDX.resolve("9.9 ОП") is None
    assert IDX.resolve("пункт 1.2.6") == "ЖБ 1.2.6"  # bare falls back to complaint rules
    assert IDX.resolve("пункт 7.7") is None


def test_invented_citation_dropped():
    r = validate_draft("Жалоба рассмотрена.", ["2.1", "7.7", "ГЗ 1.2"], IDX)
    assert r.cited == ["2.1", "GZ 1.2"]
    assert r.dropped_citations == ["7.7"]


def test_invented_rule_in_text_removed():
    r = validate_draft("Нарушен пункт 2.1 ОП. Также нарушен пункт 8.8 ОП. Спасибо.", [], IDX)
    assert "8.8" not in r.text and "2.1" in r.text
    assert r.invalid_mentions == ["8.8"]
    assert r.cited == ["2.1"]
    assert r.force_human_reasons


def test_dates_are_not_citations():
    r = validate_draft("Жалоба от 19.02 рассмотрена в 21.30.", [], IDX)
    assert r.invalid_mentions == [] and r.cited == []


def test_bare_with_context():
    assert validate_draft("Нарушение пункта 2.1 общих правил.", [], IDX).cited == ["2.1"]
    assert validate_draft("По причине 1.2.6 Правил жалоб.", [], IDX).cited == ["ЖБ 1.2.6"]


def test_promises_force_human():
    assert validate_draft("Игрок получит мут на 30 минут.", [], IDX).promises == ["punishment"]
    assert validate_draft("Мы вас разбаним.", [], IDX).promises == ["unban"]
    assert validate_draft("Мы вернём деньги.", [], IDX).promises == ["refund"]
    assert validate_draft("Уточните, пожалуйста, ваш вопрос.", [], IDX).promises == []
