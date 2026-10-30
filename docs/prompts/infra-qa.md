# Стартовый промпт — Участник 4, Infra / QA

Скопируй всё, что ниже линии, в Claude Code в папке клонированного репозитория.

---

Ты — мой напарник-разработчик на хакатоне Decentrathon 6.0. Я — **Infrastructure / QA** в команде из 4 человек.
Репозиторий: https://github.com/torgyndoshybekova-ops/support-copilot (я его уже склонировал, ты в его корне).
Данные кейса лежат у меня локально в `data/` (в git их нет и быть не должно).

1. Прочитай `CLAUDE.md`, затем `docs/TEAM.md` (раздел «Participant 4 — Infra / QA»), `docs/API.md`,
   `contract/schemas.py`, `data/README.md`. Правила из CLAUDE.md обязательны.
2. Ветка `feat/infra-make`:
   - `Makefile` с целями: `make data ARCHIVE=<путь к .gz>` (распаковать handout в `data/`, без файлов `._*`),
     `make install`, `make run` (uvicorn), `make predict IN=... OUT=...` (батч через `service.app.pipeline.triage`,
     по строке на тикет, тот же порядок, `{"ticket_id": ..., ...TriageResponse}`), `make eval`, `make test`, `make up`;
   - батч-скрипт `eval/predict.py`; он должен работать уже сейчас на заглушке пайплайна;
   - `Dockerfile` (service) и `docker-compose.yml` (service + admin-ui), `.env.example` без значений.
   Проверь `make predict IN=data/tickets_valid.jsonl OUT=/tmp/pred.jsonl` — 1000 валидных строк. Открой PR.
3. Ветка `feat/infra-eval` — `eval/eval.py`, одна команда `make eval` печатает таблицу:
   accuracy категории и macro-F1 приоритета на gold-наборе; доля невалидных цитат (id нет в `rules.json`);
   доля `needs_human`; нарушения обязательных правил (платёж/взлом/апелляция без `needs_human`);
   p50/p95 латентности; стоимость на тикет (токены × цена модели из конфига).
4. Ветка `feat/infra-labeling` — `ml/labeling/`: скрипт LLM-разметки category/priority по критериям из
   `data/taxonomy.json` для 3–5 тыс. тикетов из train (ключ из `.env`, провайдер согласовать с Tech Lead,
   кэширование ответов, ограничение скорости, подсчёт стоимости). Результат — в `data/labels/` (не в git).
   Затем инструмент для ручной проверки ~200 тикетов (простой CLI или страница) → `data/gold.jsonl`.
5. `docs/REPORT.md` — шаблон отчёта: метрики, примеры черновиков ru/kk/uz, доля «нужен человек», стоимость,
   латентность, ограничения. Цифры только из `make eval`.

Не трогай `contract/`, `service/app/` (кроме чтения), `admin-ui/`. Нужно изменение — скажи мне.
После каждого этапа кратко отчитайся: что сделано, что проверено, что дальше.
