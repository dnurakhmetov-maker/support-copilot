"""Rules index: canonical citation ids, alias parsing, lookup. Owner: Tech Lead.

Canonical id format (see docs/EDA.md):
  general rules (ОП)  -> bare point, e.g. "2.1"
  any other section   -> "<CODE> <point>", e.g. "GZ 1.2", "ЖБ 1.2.6"
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

# section id in rules.json -> (canonical code, aliases as admins/players write them)
# "" means bare ids (general rules).
SECTION_CODES: dict[int, tuple[str, tuple[str, ...]]] = {
    30: ("", ("ОП", "OP", "ОР", "OП", "ОБЩИХ", "ОБЩ")),
    31: ("ЖБ", ("ЖБ", "ППЖ", "ПЖ", "ЖАЛОБ")),
    32: ("GZ", ("GZ", "ГЗ", "ЗЗ", "GREEN")),
    33: ("ОПЛЗ", ("ОПЛЗ", "ОПЛИЗ")),
    34: ("ПГО", ("ПГО", "PGO", "НПГО")),
    35: ("ПГВД", ("ПГВД", "ПГВИД", "GW")),
    36: ("FW", ("FW", "ФВ")),
    40: ("ОПГ", ("ОПГ", "OPG")),
    41: ("BW", ("BW",)),
    42: ("FURA", ("FURA",)),
}
_ALIAS_TO_SECTION = {a: sid for sid, (_, aliases) in SECTION_CODES.items() for a in aliases}
_ALIAS_RE = "|".join(sorted((re.escape(a) for a in _ALIAS_TO_SECTION), key=len, reverse=True))
_POINT = r"\d{1,2}(?:\.\d{1,2}){1,3}"
# "2.1 ОП", "2.1ОП", "ОП 2.1", "GZ 1.2", "п. 2.1" (bare)
_PATTERNS = [
    re.compile(rf"(?P<p>{_POINT})\s*(?:(?:пункт|правил|общих)\w*\s+)?(?P<c>{_ALIAS_RE})(?![A-Za-zА-Яа-яЁё])", re.I),
    re.compile(rf"(?<![A-Za-zА-Яа-яЁё])(?P<c>{_ALIAS_RE})\s*(?:п\.?\s*)?(?P<p>{_POINT})", re.I),
    # bare number only with an explicit context word, so dates/times are never treated as citations
    re.compile(rf"(?:пункт\w*|п\.|правил\w*|причине|rule|band\w*|qoida\w*)\s*(?P<p>{_POINT})(?![\d.]*\d)", re.I),
]


@dataclass(frozen=True)
class RulePoint:
    cid: str  # canonical id
    section_id: int
    section_title: str
    ref: str
    text: str


def canonical(section_id: int, ref: str) -> str:
    code = SECTION_CODES.get(section_id, (str(section_id), ()))[0]
    return f"{code} {ref}" if code else ref


class RulesIndex:
    def __init__(self, rules_json: dict):
        self.points: dict[str, RulePoint] = {}
        for sec in rules_json["rules"]:
            sid = int(sec["id"])
            for p in sec.get("points", []):
                cid = canonical(sid, p["ref"])
                self.points[cid] = RulePoint(cid, sid, sec.get("category", ""), p["ref"], p["text"])

    def _bare(self, point: str) -> str:
        """Bare point: general rules first, then complaint rules (admins cite ЖБ points without a code)."""
        for sid in (30, 31):
            cid = canonical(sid, point)
            if cid in self.points:
                return cid
        return canonical(30, point)

    def __contains__(self, cid: str) -> bool:
        return cid in self.points

    def get(self, cid: str) -> RulePoint | None:
        return self.points.get(cid)

    def resolve(self, raw: str) -> str | None:
        """Map any citation spelling ("2.1 ОП", "gz 1.2", "ЖБ 1.2.6", "2.1") to a canonical id that exists."""
        found = self.find_all(raw)
        return found[0] if found else None

    def find_all(self, text: str) -> list[str]:
        """All existing canonical ids mentioned in free text, in order, deduplicated."""
        out: list[str] = []
        taken: list[tuple[int, int]] = []
        for pat in _PATTERNS:
            for m in pat.finditer(text):
                span = m.span()
                if any(s < span[1] and span[0] < e for s, e in taken):
                    continue
                code = m.groupdict().get("c")
                cid = canonical(_ALIAS_TO_SECTION[code.upper()], m.group("p")) if code else self._bare(m.group("p"))
                taken.append(span)
                if cid in self.points and cid not in out:
                    out.append(cid)
        return out

    def mentions(self, text: str) -> list[tuple[str, bool]]:
        """All citation-looking mentions as (canonical_id, exists). Used to catch invented rules in drafts."""
        res: list[tuple[str, bool]] = []
        taken: list[tuple[int, int]] = []
        for pat in _PATTERNS:
            for m in pat.finditer(text):
                span = m.span()
                if any(s < span[1] and span[0] < e for s, e in taken):
                    continue
                code = m.groupdict().get("c")
                taken.append(span)
                cid = canonical(_ALIAS_TO_SECTION[code.upper()], m.group("p")) if code else self._bare(m.group("p"))
                res.append((cid, cid in self.points))
        return res


@lru_cache(maxsize=1)
def load_rules(path: str | None = None) -> RulesIndex:
    p = Path(path or os.path.join(os.getenv("DATA_DIR", "data"), "rules.json"))
    return RulesIndex(json.loads(p.read_text(encoding="utf-8")))
