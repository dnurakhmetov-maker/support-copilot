# EDA — findings that drive design

Aggregate statistics only (no ticket texts — see CLAUDE.md data rules). Source: handout 2026-10-27.

## Volume and labels
- train: 118,337 tickets (67,755 complaints / 50,582 reports); valid: 1,000 (817 / 183).
- **No category/priority labels anywhere** (valid included) → we label ourselves (LLM labels + hand-checked gold).
- Complaint outcome labels exist: `verdict` (11 values), `punishment`, `violation` — extracted from admin templates.
  `verdict=other` (template not recognized) is 35% of complaints → use with care.

## Languages
- train `lang`: ru 87.8%, lat 6.2%, und 4.2%, uz 0.5%, en 0.5%, pt 0.5%, kk 0.25%.
- valid `lang`: ru 73%, uz 12%, lat 8%, kk 3%, und 2.5%, pt 1%, en 0.7% → valid over-samples uz/kk vs train.
- `lat` is mostly from `cis` region and mixed: translit Russian, Uzbek Latin, gamer slang. Needs its own detector.
- Regions: cis 97%, uz 2.2%, kz 0.5%.

## cp1251 look-alikes — important trap
- Only 95 train tickets contain look-alike chars, **83% of them are on `cis` servers** and the most frequent char is `є`.
  Most of these are **Ukrainian** (є, ї are real Ukrainian letters), not Kazakh.
- ⇒ `normalize()` must NOT blindly map є→ә, ї→ү. Map only when region is `kz` / server_lang `kk` or the text has
  other Kazakh markers. Ukrainian text should be detected (treat as ru-family / und for reply language).

## Texts
- Player text median length: complaints 30 chars (p90 155), reports 12 chars (p90 43). Very short, slangy.
- Every complaint has ≥1 screenshot (`images_count>0` = 100%) → attachment count is not an evidence signal;
  a video link (`<url>` token) is. Complaints with `<url>`: approved 33% vs 21% without; need_evidence 8% vs 14%.
- Reported player's comment present in only 4.5% of complaints.

## Admin replies
- Complaint replies are templated; outcome, punishment and violation code are embedded.
- Reports: admin replies are short, generic (greeting + "clarify your question" / "helping now" / "watching you").
  A large share of reports is noise (greetings, single symbols, insults to admins).
- **Admins decide complaints using screenshots we don't have** → we cannot know the verdict from text alone.
  Strategy: draft in the admin template style with a *proposed* decision, `needs_human=true` whenever a punishment
  or approval is involved; auto-answerable only for deterministic cases (invalid format, duplicate, noise reports,
  standard how-to questions).

## Rule citations
- `rules.json`: 11 sections, 266 points, ids are **not globally unique** (`1.1` exists in many sections).
- Admins cite as `<point> <SECTION>`: `2.1 ОП` (general rules) dominates by far; then `GZ` (green zone),
  `ЖБ` (complaint rules); slang codes DM/SK/DB/TK also appear.
- ⇒ canonical citation id: bare `2.1` = general rules (matches the case contract example);
  other sections prefixed: `GZ 1.2`, `ЖБ 1.2.6`, … Validator resolves both forms. Format to confirm with organizers.
- Player-entered `rule_refs_raw` is mostly noise (".", "1", "оск", "дм") — never trust it as a citation.

## Duplicates
- 28,143 tickets (24%) follow a previous ticket of the same reporter and kind within 60 minutes.
  One reporter filed up to 118 reports. Dedup is a real, measurable win (bonus criterion).
