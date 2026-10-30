"""normalize / detect_lang / guards / dedup. All texts are INVENTED (probe_tickets.json is the team's own file)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from contract.schemas import Message, TriageRequest
from service.app import dedup, guards
from service.app.normalize import detect_lang, normalize

PROBES = json.loads((Path(__file__).parents[1] / "app" / "llm" / "probe_tickets.json").read_text(encoding="utf-8"))


def req(text: str, kind: str = "report", **kw) -> TriageRequest:
    return TriageRequest(kind=kind, messages=[Message(role="player", text=text)], **kw)


# ---------- normalize ----------
def test_lookalikes_mapped_only_on_kz_server():
    s = "Кјмектесіѕізші, текстураґа тўрып ќалдым, үй є, її"
    assert normalize(s, region="kz") == "Көмектесіңізші, текстураға тұрып қалдым, үй ә, үү"
    assert normalize(s, server_lang="kk") == normalize(s, region="kz")
    assert normalize(s, region="KZ") == normalize(s, region="kz")


@pytest.mark.parametrize("region,lang", [("cis", "ru"), (None, None), ("uz", "uz")])
def test_ukrainian_letters_untouched_elsewhere(region, lang):
    s = "Він мене вбив, є відео, її ґанок"
    assert normalize(s, region, lang) == s


def test_cleanup():
    assert normalize("  при​вет  мир \t\n\n\n  второй ряд  ") == "привет мир\nвторой ряд"
    assert normalize("o‘yinchi gʻalaba bo’ladi") == "o'yinchi g'alaba bo'ladi"
    assert normalize("") == "" and normalize("\x00\x07") == ""


# ---------- detect_lang ----------
@pytest.mark.parametrize("t", PROBES, ids=[t["ticket_id"] for t in PROBES])
def test_probe_tickets(t):
    text = normalize(t["messages"][0]["text"], t["region"], t["server_lang"])
    assert detect_lang(text, t["region"], t["server_lang"]) == t["lang"]
    assert detect_lang(text) == t["lang"]  # also without the region hint (current pipeline call)


@pytest.mark.parametrize("text,expected", [
    ("помогите застрял в текстурах", "ru"),
    ("игрок обозвал меня, видео есть", "ru"),
    ("допоможіть, мене забанили ні за що", "ru"),  # Ukrainian -> ru
    ("мені потрібна допомога з машиною", "ru"),   # Ukrainian with only "і"
    ("Він мене вбив без причини, є відео", "ru"),
    ("үй сатып алдым бірақ кілт жоқ", "kk"),
    ("мен ойынға кіре алмаймын", "kk"),
    ("қалай ақша табуға болады", "kk"),          # only shared letters, Kazakh words
    ("ўйинчи мени ҳақорат қилди", "uz"),
    ("Ёрдам беринг, машинам ағдарилиб қолди", "uz"),
    ("илтимос ёрдам беринг", "uz"),
    ("bu o'yinchi chatda onamni so'kdi", "uz"),
    ("o‘yinchi meni haqorat qildi", "uz"),
    ("iltimos yordam bering mashinam qolib ketdi", "uz"),
    ("please help, my car is stuck", "en"),
    ("I was banned for no reason", "en"),
    ("pomogite pozhaluista zastryal", "ru"),      # Russian in Latin letters
    ("ajuda por favor minha conta", "und"),      # pt is outside the contract set
    ("😡😡😡", "und"), ("123 456", "und"), ("<url>", "und"), ("", "und"),
])
def test_detect_lang_text_only(text, expected):
    assert detect_lang(text) == expected


@pytest.mark.parametrize("text,region,server_lang,expected", [
    ("помогите пожалуйста", "kz", "kk", "ru"),       # Russian on a kz server stays ru
    ("мен келдім", "kz", "kk", "kk"),               # one Kazakh letter is enough on kz
    ("акаунтимни бузишди", "uz", "uz", "uz"),        # Uzbek suffixes on a uz server
    ("помогите застрял", "uz", "uz", "ru"),
    ("машина қолди", "uz", "uz", "uz"),             # shared letter on uz -> uz
    ("машина қалды", "kz", "kk", "kk"),             # same letter on kz -> kk
])
def test_detect_lang_region_hint(text, region, server_lang, expected):
    assert detect_lang(normalize(text, region, server_lang), region, server_lang) == expected


# ---------- guards ----------
@pytest.mark.parametrize("text,topic", [
    ("задонатил 500 рублей, донат не пришёл", "платёж"),
    ("оплатил пропуск, а ничего не начислили", "платёж"),
    ("Донат төледім, бірақ ештеңе түспеді", "платёж"),
    ("to'lov qildim lekin kelmadi", "платёж"),
    ("I paid but got nothing", "платёж"),
    ("верните деньги за кейс", "возврат"),
    ("хочу компенсацию за пропавшую машину", "возврат"),
    ("меня взломали, всё имущество пропало", "взлом"),
    ("кто-то сменил пароль от моего аккаунта", "взлом"),
    ("не могу войти в аккаунт, почта чужая", "взлом"),
    ("Аккаунтымды бұзып алды", "взлом"),
    ("akkauntimni buzib olishdi, parolni o'zgartirishdi", "взлом"),
    ("my account was hacked yesterday", "взлом"),
    ("разбаньте пожалуйста, больше не буду", "разбан"),
    ("снимите мут, я ничего не писал", "разбан"),
    ("меня забанили ни за что", "обжалование"),
    ("не согласен с наказанием, прошу пересмотреть", "обжалование"),
    ("Әкімші маған себепсіз бан берді, келіспеймін, қайта қарауды сұраймын", "обжалование"),
    ("admin menga sababsiz ban berdi, rozi emasman", "обжалование"),
    ("I was banned for no reason, appeal", "обжалование"),
])
def test_guards_trigger(text, topic):
    reasons = guards.check(req(text), "report.question")
    assert any(topic in r for r in reasons), reasons
    assert all(r.startswith("обязательно человеку: ") for r in reasons)


@pytest.mark.parametrize("text,kind", [
    ("как поменять цвет машины?", "report"),
    ("игрок оскорбил меня в чате, видео есть", "complaint"),
    ("он использует hack, летает по карте", "complaint"),          # cheat complaint, not account hacking
    ("Ол ережені бұзды, чатта балағаттады", "complaint"),          # "broke the rules", not hacking
    ("списался с админом, жду ответа", "report"),
    ("он убил меня ни за что", "complaint"),                       # not about own punishment
    ("где найти работу полицейского?", "report"),
])
def test_guards_quiet(text, kind):
    assert guards.check(req(text, kind=kind, region="kz" if "ә" in text else None), f"{kind}.question") == []


def test_guards_category_and_dedup_of_reasons():
    r = guards.check(req("донат не пришёл, верните оплату"), "report.payment")
    assert r[0] == "обязательно человеку: платёж/донат — проверку оплаты делает администратор"
    assert len(r) == len(set(r)) and any("возврат" in x for x in r)
    assert guards.check(req("ничего особенного"), "report.account") != []


def test_guards_ignore_non_player_messages():
    r = TriageRequest(kind="complaint", messages=[
        Message(role="player", text="он оскорблял меня"),
        Message(role="reported_player", text="разбаньте меня, верните донат"),
        Message(role="admin", text="по вопросам оплаты пишите в поддержку"),
    ])
    assert guards.check(r, "complaint.insult") == []


def test_guards_lookalikes_on_kz():
    assert guards.check(req("Донат тјледім", region="kz"), "report.question")


# ---------- dedup ----------
@pytest.fixture(autouse=True)
def _fresh_dedup():
    dedup.reset()
    yield
    dedup.reset()


def d(text, at, rh="rh_1", tid=None, **kw):
    return dedup.check(req(text, reporter_hash=rh, created_at=at, ticket_id=tid, **kw))


def test_repeat_within_hour_is_duplicate():
    assert d("помогите застрял в текстурах на заводе", "2026-10-30T10:00:00Z", tid="a1") is None
    assert d("Помогите!!! застрял в текстурах на заводе", "2026-10-30T10:20:00Z", tid="a2") == "a1"
    assert d("помогите застрял в текстурах на заводе пожалуйста", "2026-10-30T10:59:00Z", tid="a3") == "a1"


def test_outside_window_or_other_reporter_is_not():
    assert d("помогите застрял в текстурах", "2026-10-30T10:00:00Z", tid="b1") is None
    assert d("помогите застрял в текстурах", "2026-10-30T11:01:00Z", tid="b2") is None   # 61 min
    assert d("помогите застрял в текстурах", "2026-10-30T11:02:00Z", rh="rh_2", tid="b3") is None


def test_different_text_is_not():
    assert d("как поменять номер машины", "2026-10-30T10:00:00Z", tid="c1") is None
    assert d("меня взломали, верните аккаунт", "2026-10-30T10:05:00Z", tid="c2") is None


def test_short_texts_match_only_exactly():
    assert d("помогите", "2026-10-30T10:00:00Z", tid="s1") is None
    assert d("помогите", "2026-10-30T10:01:00Z", tid="s2") == "s1"
    assert d("да", "2026-10-30T10:03:00Z", tid="s4") is None


def test_no_reporter_hash_never_duplicate():
    assert dedup.check(req("одинаковый текст жалобы")) is None
    assert dedup.check(req("одинаковый текст жалобы")) is None


def test_same_ticket_reprocessed_is_not_its_own_duplicate():
    assert d("застрял в текстурах у банка", "2026-10-30T10:00:00Z", tid="r1") is None
    assert d("застрял в текстурах у банка", "2026-10-30T10:00:00Z", tid="r1") is None


def test_different_accused_player_is_new_complaint():
    t = "оскорблял меня и мою семью в чате, видео <url>"
    assert d(t, "2026-10-30T10:00:00Z", tid="n1", reported_nick="Test_Alpha") is None
    assert d(t, "2026-10-30T10:05:00Z", tid="n2", reported_nick="Test_Beta") is None
    assert d(t, "2026-10-30T10:06:00Z", tid="n3", reported_nick="test_alpha") == "n1"


def test_without_ids_and_times_uses_now_and_description():
    assert d("застрял под картой возле спавна", None) is None
    out = d("застрял под картой возле спавна", None)
    assert out and out.startswith("обращение того же игрока от ")


def test_naive_and_offset_times():
    assert d("машина пропала из гаража после рестарта", "2026-10-30 10:00:00", tid="t1") is None
    assert d("машина пропала из гаража после рестарта", "2026-10-30T15:30:00+05:00", tid="t2") == "t1"  # 10:30Z


def test_kz_lookalikes_unified():
    assert d("Кјмектесіѕізші, текстураґа тўрып ќалдым", "2026-10-30T10:00:00Z", tid="k1", region="kz") is None
    assert d("Көмектесіңізші, текстураға тұрып қалдым", "2026-10-30T10:10:00Z", tid="k2", region="kz") == "k1"
