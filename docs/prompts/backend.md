# Стартовый промпт — Участник 2, Backend

Скопируй всё, что ниже линии, в Claude Code в папке клонированного репозитория.

---

Ты — мой напарник-разработчик на хакатоне Decentrathon 6.0. Я — **Backend Developer** в команде из 4 человек.
Репозиторий: https://github.com/torgyndoshybekova-ops/support-copilot (я его уже склонировал, ты в его корне).

1. Прочитай `CLAUDE.md`, затем `docs/TEAM.md` (раздел «Participant 2 — Backend»), `docs/API.md`,
   `contract/schemas.py`, `service/app/pipeline.py`. Правила из CLAUDE.md обязательны.
2. Создай ветку `feat/backend-api`.
3. Задача 1 (срочно, это нужно всей команде): FastAPI-сервис в `service/app/main.py` (+ `service/app/api/` при необходимости):
   - `POST /triage` — принимает `TriageRequest`, вызывает `service.app.pipeline.triage()`, возвращает `TriageResponse`;
   - `GET /health`;
   - ошибки строго по таблице из `docs/API.md`; `/triage` не должен падать с 500 из-за LLM/пайплайна — деградирует в
     ответ с `needs_human=true` и причиной;
   - CORS для `http://localhost:3000` (админка);
   - `service/requirements.txt`, `__init__.py` где нужно; запуск из корня: `uvicorn service.app.main:app --port 8000`.
   - pytest-тесты в `service/tests/` на выдуманных примерах (не из данных кейса!).
   Проверь curl-примером из API.md и pytest. Открой PR.
4. Задача 2 (ветка `feat/backend-admin`): эндпоинты админки из API.md — `POST /tickets`, `GET /queue`,
   `GET /tickets/{id}`, `POST /tickets/{id}/action`, `GET /stats`; хранение в SQLite (`DB_PATH` из env,
   по умолчанию `data/app.sqlite`). Каждое действие админа пишется в `history` — это обучающий сигнал.
5. Задача 3 (ветка `feat/backend-preprocess`): модули, которые потом подключит Tech Lead:
   - `service/app/normalize.py`: `normalize(text) -> str` (замена cp1251-двойников ќ→қ, ґ→ғ, ѕ→ң, є→ә, ј→ө, ў→ұ, ї→ү —
     только в словах, где это казахский, не ломай русский/узбекский текст), `detect_lang(text) -> Lang`
     (ru/kk/uz/en/und; учти узбекский на латинице);
   - `service/app/guards.py`: `check(req, category) -> list[str]` — причины обязательной передачи человеку
     (платежи, апелляции банов, взлом, возврат, разбан);
   - `service/app/dedup.py`: тот же `reporter_hash` + похожий текст в окне 60 минут → дубль.
   Для каждого модуля — тесты. Данные кейса лежат локально в `data/` — по ним можно мерить, но не коммитить.

Не трогай `contract/`, `pipeline.py`, `classify.py`, `rag/`, `llm/`, `validate.py`, `admin-ui/`, `eval/`, `Makefile`.
Нужно изменение контракта — скажи мне, я передам Tech Lead.
После каждого этапа кратко отчитайся: что сделано, что проверено, что дальше.
