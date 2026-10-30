# Участник 2 — Backend. Старт за 10 минут

## Шаг 1. Подготовка (терминал на своём компьютере)

1. Прими приглашение в репозиторий (придёт на почту / в уведомления GitHub).
2. Склонируй репозиторий и создай окружение Python 3.12:
```bash
cd ~ && git clone https://github.com/torgyndoshybekova-ops/support-copilot.git && cd support-copilot
curl -LsSf https://astral.sh/uv/install.sh | sh && source $HOME/.local/bin/env
uv venv --python 3.12 .venv && source .venv/bin/activate
```
3. Данные кейса (архив `handout.gz` пришлёт Tech Lead — **в git его не кладём никогда**):
```bash
mkdir -p data && tar -xzf ~/Downloads/handout.gz --strip-components=1 -C data --exclude='._*' && ls data
```
4. Запусти Claude Code в корне репозитория: `claude` — и вставь промпт ниже целиком.

## Шаг 2. Промпт для Claude (скопируй всё ниже линии)

---

Ты — мой напарник-разработчик на хакатоне Decentrathon 6.0. Я — **Backend Developer** в команде из 4 человек.
Мы в корне репозитория https://github.com/torgyndoshybekova-ops/support-copilot. Python-окружение `.venv` (3.12) уже создано и активировано, данные кейса лежат в `data/` (git-ignored).

Сначала прочитай `CLAUDE.md`, затем `docs/TEAM.md` (раздел «Participant 2 — Backend»), `docs/API.md`, `docs/EDA.md`,
`contract/schemas.py`, `service/app/pipeline.py`. Правила из CLAUDE.md обязательны: только мои файлы, своя ветка + PR,
данные и ключи не коммитить, тесты только на выдуманных примерах.

Контекст: Tech Lead уже сделал `service/app/pipeline.py` — функция `triage(req) -> TriageResponse` (поиск, LLM, валидатор).
Пайплайн **не трогаем и не переписываем**, только вызываем. В нём есть временные запасные версии normalize/guards —
как только появятся мои модули, пайплайн автоматически начнёт использовать их.

**Задача 1 (срочно, ветка `feat/backend-api`)** — FastAPI-сервис:
- `service/app/main.py`: `POST /triage` (TriageRequest → `pipeline.triage()` → TriageResponse), `GET /health`;
- ошибки строго по таблице в `docs/API.md`; `/triage` никогда не отдаёт 500 из-за пайплайна — ловим исключение и
  возвращаем валидный ответ с `needs_human=true` и причиной;
- прогрев на старте (lifespan): вызвать `service.app.rag.cases_index.load_cases()` и `service.app.rag.rules_index.load_rules()`,
  чтобы первый запрос не ждал ~10 с построения индекса;
- CORS для `http://localhost:3000`;
- `service/requirements.txt` (fastapi, uvicorn, pydantic, scikit-learn, numpy, pytest, httpx);
- запуск из корня: `uvicorn service.app.main:app --port 8000`;
- pytest в `service/tests/test_api.py` (выдуманные тексты). Проверь curl-примером из API.md. Открой PR.
  Для тестов без ключа LLM: `LLM_PROVIDER=none` — пайплайн вернёт валидный ответ «нужен человек».

**Задача 2 (ветка `feat/backend-admin`)** — API админки из `docs/API.md`: `POST /tickets`, `GET /queue`, `GET /tickets/{id}`,
`POST /tickets/{id}/action`, `GET /stats`; хранение в SQLite (`DB_PATH`, по умолчанию `data/app.sqlite`).
Каждое действие админа (send / edit / escalate + итоговый текст) пишется в `history` — это обучающий сигнал. Тесты.

**Задача 3 (ветка `feat/backend-preprocess`)** — модули, которые пайплайн подхватит автоматически. **Сигнатуры строго такие:**
- `service/app/normalize.py`:
  - `normalize(text: str, region: str | None = None, server_lang: str | None = None) -> str` — чистка текста и замена
    cp1251-двойников ќ→қ, ґ→ғ, ѕ→ң, є→ә, ј→ө, ў→ұ, ї→ү **только** если `region == "kz"` или `server_lang == "kk"`
    или в тексте есть другие признаки казахского. Большинство є/ї в данных — настоящий украинский на cis-серверах
    (см. `docs/EDA.md`), его ломать нельзя;
  - `detect_lang(text: str) -> str` — одно из `ru | kk | uz | en | und`; узбекский бывает и кириллицей, и латиницей
    (o', g', sh, ch, qanday, iltimos…). Измерь точность на `data/tickets_valid.jsonl` (поле `lang`) и напиши цифру в PR.
- `service/app/guards.py`: `check(req: TriageRequest, category: str) -> list[str]` — список причин обязательной передачи
  человеку на русском (платежи/донат, апелляция своего наказания, взлом/потеря доступа, возврат, разбан). Пусто — если причин нет.
- `service/app/dedup.py`: `is_duplicate(req, history) -> bool` — тот же `reporter_hash` + похожий текст в окне 60 минут.
  В данных 24% тикетов — повтор от того же игрока в течение часа.

Не трогай `contract/`, `pipeline.py`, `rag/`, `llm/`, `validate.py`, `admin-ui/`, `eval/`, `Makefile`.
Нужно изменение контракта — скажи мне, я передам Tech Lead.
После каждой задачи: `git fetch origin && git rebase origin/main`, пуш ветки, PR, и кратко отчитайся мне: что сделано,
что проверено (с цифрами), что дальше.
