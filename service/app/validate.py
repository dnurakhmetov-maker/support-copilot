"""Post-LLM validator. Owner: Tech Lead.

Guarantees (deterministic, independent of the LLM):
  1. every id in draft.cited exists in rules.json (canonicalized);
  2. the draft text does not mention any rule id that doesn't exist;
  3. promises of unban / refund / compensation / punishment of another player force needs_human=true.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from service.app.rag.rules_index import RulesIndex

# Promises the service may never make on its own (case constraint).
_PROMISES = {
    "unban": re.compile(r"\b(разбан\w*|разблокир\w*|снимем бан|бан будет снят|unban\w*)", re.I),
    "refund": re.compile(r"\b(верн[её]м|возврат\w*|компенс\w*|возместим|refund\w*|qaytar\w*)", re.I),
    "punishment": re.compile(
        r"(получит\s+(наказание|мут|бан|варн|джайл|деморган)|будет\s+(наказан|заблокирован|забанен|замучен)"
        r"|выдан[оаы]?\s+(наказание|мут|бан|варн)|\bмут\s+на\s+\d+|\bбан\w*\s+на\s+\d+|\bjazo\w*)",
        re.I,
    ),
}


@dataclass
class ValidationResult:
    cited: list[str]
    text: str
    dropped_citations: list[str] = field(default_factory=list)
    invalid_mentions: list[str] = field(default_factory=list)
    promises: list[str] = field(default_factory=list)

    @property
    def force_human_reasons(self) -> list[str]:
        r = []
        if self.promises:
            r.append("черновик содержит решение, которое подтверждает администратор: " + ", ".join(self.promises))
        if self.invalid_mentions:
            r.append("из черновика удалены несуществующие пункты правил: " + ", ".join(self.invalid_mentions))
        return r


def _strip_invalid_mentions(text: str, index: RulesIndex) -> tuple[str, list[str]]:
    bad = [cid for cid, ok in index.mentions(text) if not ok]
    if not bad:
        return text, []
    # drop whole sentences that contain an invented rule reference
    sentences = re.split(r"(?<=[.!?])\s+", text)
    kept = [s for s in sentences if all(ok for _, ok in index.mentions(s))]
    return " ".join(kept).strip(), sorted(set(bad))


def validate_draft(text: str, cited: list[str], index: RulesIndex) -> ValidationResult:
    canon: list[str] = []
    dropped: list[str] = []
    for c in cited:
        cid = c if c in index else index.resolve(c)
        if cid and cid not in canon:
            canon.append(cid)
        elif not cid:
            dropped.append(c)

    clean_text, invalid = _strip_invalid_mentions(text, index)
    # citations mentioned in text but missing from `cited` are added (they exist — checked above)
    for cid in index.find_all(clean_text):
        if cid not in canon:
            canon.append(cid)

    promises = [name for name, rx in _PROMISES.items() if rx.search(clean_text)]
    return ValidationResult(canon, clean_text, dropped, invalid, promises)
