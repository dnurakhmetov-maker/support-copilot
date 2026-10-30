"""Mandatory hand-off guards. Owner: Backend.

check(req, category) -> list[str]
    Russian reasons why a human admin must handle the ticket; empty list if none.
    Topics (case rule): payments / donations, refunds, appeals of the player's OWN punishment, unban requests,
    account hacking / lost access. Only the player's own messages are scanned (not admin or accused-player text).
    Patterns cover ru, kk, uz (Latin + Cyrillic) and en; text goes through normalize() first so kz look-alikes
    and Uzbek apostrophes are unified.

Deterministic and conservative: a false "needs human" costs an admin a click, a missed one can cost money/trust.
"""
from __future__ import annotations

import re

from contract.schemas import TriageRequest
from service.app.normalize import normalize

_F = re.I | re.U

PAYMENT = re.compile(
    r"(донат|задонат|оплат|оплачи|плат[её]ж|пополн\w* (баланс|сч[её]т|донат)|списал\w* (деньг|средств|\d)|списани\w* (деньг|средств)|транзакц|\bчек\b"
    r"|деньги (не )?(при|за|сп)|не (при|за)шл\w* (донат|деньги|коин|монет|валют)|купил\w* (донат|валют|коин)"
    r"|төле[дм]|төлем|ақша\w* (түспе|кет|алын)"           # kk
    r"|to'lov|to'la(dim|di|gan)|pul (tushma|kel|ket|yech)|тўлов|тўла(дим|ди)|пул (туш|кел|кет)"  # uz
    r"|payment|paid|purchase|charged|donat(e|ion))", _F)
REFUND = re.compile(
    r"(возврат|верн\w* (мне )?(деньг|донат|средств|оплат)|вернуть (деньг|донат|средств)|компенс"
    r"|ақша\w* қайтар|қайтарыңыз|pul\w* qaytar|qaytarib bering|пул\w* қайтар|refund|money back)", _F)
HACK = re.compile(
    r"(взлом|взломал|украл\w* (акк|аккаунт)|угнал\w* (акк|аккаунт)|(сменил|поменял|изменил)\w* пароль"
    r"|пароль (не подходит|сменили|украли)|не могу (войти|зайти) в (акк|аккаунт)|нет доступа к (акк|аккаунт)"
    r"|(потерял|утерял)\w* доступ|восстанов\w* (акк|аккаунт|доступ)|чужой (вход|ip)"
    r"|аккаунт\w* (бұз|кіре алмай|ұрла)|бұзып ал"                         # kk
    r"|akkaunt\w* buz|buzib olish|parol\w* (o'zgartir|almashtir)|akkaunt\w* (o'g'irla|kira olmay)"  # uz Latin
    r"|акк?аунт\w* буз|бузиб олиш|парол\w* (ўзгартир|алмаштир)"                       # uz Cyrillic
    r"|(got|been|was|were) hacked|hacked (my|the) account|stolen account|account (was )?stolen|can'?t (log ?in|access my account)|lost access)", _F)
UNBAN = re.compile(
    r"(разбан|разбань|разблокир|сним\w* (с меня )?(бан|блок|мут|варн|джайл|деморган|наказани)"
    r"|бан\w* (алып таст|ал(ыңыз|ып бер))|bandan (chiqar|oling)|unban)", _F)
APPEAL = re.compile(
    r"(не согласен|не согласна|обжал|апелляц|пересмотр|несправедлив\w* (бан|мут|варн|наказ|блок)"
    r"|(выдал\w*|дали|получил\w*|забанил\w*|замутил\w*|заблокировал\w*|посадил\w*) (мне |меня )?"
    r"(\w+ )?(без причин|ни за что|просто так|по ошибке)"
    r"|(меня|мне) (забанил|замутил|заблокировал|кикнул)\w* (без причин|ни за что|просто так)"
    # frequent short forms in real appeals (tuned on train, see PR): "за что бан", "не согл", "опру", "докву"
    r"|за что (мне |меня |у меня )?(бан|мут|варн|джайл|деморган|кик|блок|наказ|посадил|забанил|замутил)"
    r"|не согл\b|\bопровержени|\bопру\b|\bдокв\w*|\bдок-?ва (на |для )?(бан|мут|варн|наказ)"
    r"|(бан|мут|варн|джайл)\w* без (причин|предупрежд)|без причин\w* (дал|выдал)\w* (бан|мут|варн|джайл)"
    r"|келіспеймін|қайта қара|себепсіз (бан|блок|мут)"                            # kk
    r"|rozi emasman|nohaq|sababsiz (ban|blok|mut)|рози эмасман|сабабсиз (бан|блок|мут)"  # uz
    r"|unfair (ban|mute|punishment)|appeal|banned (me )?for no reason|wrongly banned)", _F)

TOPICS: list[tuple[str, re.Pattern]] = [
    ("платёж/донат — проверку оплаты делает администратор", PAYMENT),
    ("запрос возврата/компенсации — решает только администратор", REFUND),
    ("взлом или потеря доступа к аккаунту — нужна проверка администратором", HACK),
    ("просьба о разбане/снятии наказания — решает только администратор", UNBAN),
    ("обжалование своего наказания — решает только администратор", APPEAL),
]
# Categories that by themselves mean a mandatory hand-off (codes are identifiers, not case data).
CATEGORY_REASONS = {
    "report.payment": "платёж/донат — проверку оплаты делает администратор",
    "report.account": "взлом или потеря доступа к аккаунту — нужна проверка администратором",
}


def player_text(req: TriageRequest) -> str:
    return normalize(" ".join(m.text for m in req.messages if m.role == "player"), req.region, req.server_lang)


def check(req: TriageRequest, category: str) -> list[str]:
    text = player_text(req)
    reasons = [reason for reason, rx in TOPICS if rx.search(text)]
    if category in CATEGORY_REASONS:
        reasons.insert(0, CATEGORY_REASONS[category])
    return [f"обязательно человеку: {r}" for r in dict.fromkeys(reasons)]
