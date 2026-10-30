"""Text cleanup and language detection. Owner: Backend.

normalize(text, region, server_lang) -> str
    Unicode/whitespace cleanup; cp1251 look-alikes (ќ ґ ѕ є ј ў ї) -> Kazakh letters ONLY on Kazakh servers
    (region == "kz" or server_lang == "kk"). Elsewhere є/ї/ґ are real Ukrainian letters (docs/EDA.md).

detect_lang(text, region=None, server_lang=None) -> "ru" | "kk" | "uz" | "en" | "und"
    Letter- and word-based heuristics, no model, microseconds per call:
      * Kazakh-only letters: ә ң ө ұ ү һ (+ і, which Uzbek lacks but Ukrainian has);
      * Uzbek-only Cyrillic letters: ў ҳ; ғ қ are shared by kk and uz;
      * Uzbek Latin: o'/g' and frequent words; English / Russian translit by word lists;
      * Ukrainian (є ї ґ, or Ukrainian words) -> "ru": we reply in Russian;
      * server region is a strong hint: on a kz server one Kazakh letter is enough, on a uz server ғ/қ mean uz.
    region/server_lang are optional, so detect_lang(text) keeps working on text alone.
"""
from __future__ import annotations

import re
import unicodedata

LANGS = ("ru", "kk", "uz", "en", "und")

# ---------- normalize ----------
_LOOKALIKE = str.maketrans({"ќ": "қ", "Ќ": "Қ", "ґ": "ғ", "Ґ": "Ғ", "ѕ": "ң", "Ѕ": "Ң", "є": "ә", "Є": "Ә",
                            "ј": "ө", "Ј": "Ө", "ў": "ұ", "Ў": "Ұ", "ї": "ү", "Ї": "Ү"})
# Uzbek Latin apostrophe variants (oʻ gʻ o‘ o’ o`) -> plain ASCII apostrophe
_APOSTROPHES = str.maketrans({"ʻ": "'", "ʼ": "'", "‘": "'", "’": "'", "`": "'", "´": "'"})
_INVISIBLE = re.compile(r"[​-‏⁠-⁤﻿­]")
_SPACES = re.compile(r"[ \t  -   　]+")
_BLANK_LINES = re.compile(r"\n\s*\n+")


def is_kz_server(region: str | None, server_lang: str | None) -> bool:
    return (region or "").lower() == "kz" or (server_lang or "").lower() == "kk"


def normalize(text: str, region: str | None = None, server_lang: str | None = None) -> str:
    if not text:
        return ""
    t = unicodedata.normalize("NFC", text)
    t = _INVISIBLE.sub("", t).replace("\r\n", "\n").replace("\r", "\n")
    t = "".join(ch for ch in t if ch == "\n" or unicodedata.category(ch)[0] != "C")  # control chars
    t = t.translate(_APOSTROPHES)
    if is_kz_server(region, server_lang):
        t = t.translate(_LOOKALIKE)
    t = _SPACES.sub(" ", t)
    t = _BLANK_LINES.sub("\n", t)
    return "\n".join(line.strip() for line in t.split("\n")).strip()


# ---------- detect_lang ----------
_KK_ONLY = set("әңөұүһ")
_UZ_ONLY = set("ўҳ")
_SHARED = set("ғқ")
_UK = set("єїґ")
_CYR_EXTRA = _KK_ONLY | _UZ_ONLY | _SHARED | _UK | set("ёі")

_WORD = re.compile(r"[a-zа-яё'әңөұүһўҳғқіієїґ]+")


def _words(*items: str) -> frozenset[str]:
    return frozenset(w for s in items for w in s.split())


# Kazakh words that need no special letter (Kazakh text with only "і" is ambiguous with Ukrainian)
KK_WORDS = _words("бар жоқ мен сен ол біз сіз маған саған оған сені оны неге қалай үшін және бірақ",
                  "себепсіз керек жоқ рахмет сәлем көмектесіңізші көмектесіңіз ойыншы ойын аккаунтым",
                  "берді алды кетті болды болады жатыр тұрып қалдым ақша алмаймын")
UK_WORDS = _words("що як це та від дуже будь ласка мене мій моя чому коли тут який яка гравець гра",
                  "допоможіть допоможи потрібно хочу можу не працює дякую привіт вже ще або зараз",
                  "його її їх ти ви ми він вона ні і й з потрібна допомога допомогу")
# Uzbek Cyrillic words without ў/ҳ/ғ/қ (otherwise indistinguishable from Russian)
UZ_CYR_WORDS = _words("ёрдам беринг беринглар илтимос керак салом рахмат раҳмат нима нега менга мени сизга",
                      "учун билан эмас эди бор йўқ йук акаунтим аккаунтим ўйин уйин ишга",
                      "килиб колди булди бўлди")
UZ_LAT_WORDS = _words("qanday nega nima iltimos yordam rahmat raxmat salom kerak bor yo'q yoq menga mening",
                      "meni sizga sizni uchun bilan emas edi qildi qiling bering beringlar akkaunt akkauntim",
                      "akkauntimni o'yin o'yinchi oyinchi ishga bo'ladi boladi bo'ldi qoldi qolib qolgan",
                      "mashina mashinam buzib parol parolni chatda onamni skrinshot ham juda endi hozir",
                      "adminlar nimaga qachon qayerda bu siz biz men")
EN_WORDS = _words("the is are was were please help my me i you your how what why when where can can't cannot",
                  "account stuck player game hello hi banned ban unban hacked password money paid",
                  "not don't doesn't it this that he she they with for from have has get got thanks thank")
RU_TRANSLIT = _words("privet zdravstvuyte zdravstvuite pomogite pomogi pozhaluista pozhaluysta pojaluista",
                     "pochemu kak chto eto menya mne ya on ona ne net da zastryal zabanili",
                     "igrok igroka mashina mashinu dengi donat slomalsya vernite spasibo",
                     "ochen tut kogda gde nado mozhno nelzya oskorblyal oskorbil ubil ubili prosto")
PT_ES_WORDS = _words("não nao obrigado obrigada por favor ajuda meu minha conta você voce jogo jogador",
                     "hola gracias ayuda mi cuenta jugador porque está esta pero")

_UZ_LAT_MARK = re.compile(r"\b\w*[og]'\w+|\b\w+[og]'\b")  # o'yin, g'alaba, bo'ladi
_Q_NOT_U = re.compile(r"q(?!u)")
# Uzbek Cyrillic suffixes that Russian words never end with: акаунтимни, бузишди, ишлаяпти, билмайман
_UZ_CYR_SUFFIX = re.compile(r"\b[а-яё]{2,}(?:имни|ишди|япти|япман|майман|ингиз)\b")


def _score(tokens: list[str], vocab: frozenset[str]) -> int:
    return sum(t in vocab for t in tokens)


def _cyrillic(low: str, tokens: list[str], region: str, server_lang: str) -> str:
    kk_only = sum(ch in _KK_ONLY for ch in low)
    uz_only = sum(ch in _UZ_ONLY for ch in low)
    shared = sum(ch in _SHARED for ch in low)
    i_count = low.count("і")
    uk = any(ch in _UK for ch in low)

    if region == "kz" or server_lang == "kk":
        # look-alikes are already mapped by normalize(); one Kazakh letter is enough here
        if kk_only or shared or i_count or uz_only or _score(tokens, KK_WORDS) >= 2:
            return "kk"
        return "ru"
    if region == "uz" or server_lang == "uz":
        if kk_only >= 2 and not uz_only:
            return "kk"
        if uz_only or shared or _score(tokens, UZ_CYR_WORDS) or _UZ_CYR_SUFFIX.search(low):
            return "uz"
        return "ru"

    kk_w, uk_w = _score(tokens, KK_WORDS), _score(tokens, UK_WORDS)
    uz_w = _score(tokens, UZ_CYR_WORDS) + len(_UZ_CYR_SUFFIX.findall(low))
    # one stray special letter in otherwise Russian text is a typo/keyboard slip, not a language (seen in valid)
    stray = kk_only + uz_only + shared == 1 and not i_count and not (kk_w or uz_w)
    if stray:
        return "ru"
    if uz_only and not kk_only:
        return "uz"
    if kk_only and not uk:
        return "kk"
    if kk_only >= 2:  # Kazakh letters outweigh a stray Ukrainian-looking one
        return "kk"
    if shared:  # Uzbek has no "і"; without it, decide by words
        return "kk" if i_count or kk_w > uz_w else "uz"
    if uk:
        return "ru"  # Ukrainian -> reply in Russian
    if i_count and kk_w > uk_w:
        return "kk"
    if not i_count and uz_w >= 2:
        return "uz"
    return "ru"


def _latin(low: str, tokens: list[str], region: str, server_lang: str) -> str:
    uz = _score(tokens, UZ_LAT_WORDS) + 2 * len(_UZ_LAT_MARK.findall(low)) + (1 if len(_Q_NOT_U.findall(low)) >= 2 else 0)
    en = _score(tokens, EN_WORDS)
    ru = _score(tokens, RU_TRANSLIT)
    pt = _score(tokens, PT_ES_WORDS)
    if region == "uz" or server_lang == "uz":
        uz += 1
    best = max(uz, en, ru, pt)
    if best == 0:
        return "und"
    if uz == best:
        return "uz"
    if pt == best and pt > en:
        return "und"  # pt/es are outside the contract's language set
    if en == best:
        return "en"
    return "ru"  # Russian written in Latin letters: reply in Russian


# Not language evidence: data placeholders (<url>, <digits>), links, Latin game nicks (First_Last7)
_NOT_LANG = re.compile(r"<[a-z_]+>|https?://\S+|www\.\S+|\b[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+\b")


def detect_lang(text: str, region: str | None = None, server_lang: str | None = None) -> str:
    if not text:
        return "und"
    low = _NOT_LANG.sub(" ", text).lower().translate(_APOSTROPHES)
    region, server_lang = (region or "").lower(), (server_lang or "").lower()
    cyr = sum(("а" <= ch <= "я") or ch in _CYR_EXTRA for ch in low)
    lat = sum("a" <= ch <= "z" for ch in low)
    if cyr == 0 and lat == 0:
        return "und"
    tokens = _WORD.findall(low)
    if cyr >= lat:
        return _cyrillic(low, tokens, region, server_lang)
    return _latin(low, tokens, region, server_lang)
