# Участник 4 — Infra / QA. Старт за 10 минут

## Шаг 1. Подготовка (терминал на своём компьютере)

1. Прими приглашение в репозиторий (придёт на почту / в уведомления GitHub).
2. Склонируй репозиторий и создай окружение Python 3.12:
```bash
cd ~ && git clone https://github.com/torgyndoshybekova-ops/support-copilot.git && cd support-copilot
curl -LsSf https://astral.sh/uv/install.sh | sh && source $HOME/.local/bin/env
uv venv --python 3.12 .venv && source .venv/bin/activate && uv pip install pydantic scikit-learn numpy
```
3. Данные кейса (архив `handout.gz` пришлёт Tech Lead — **в git его не кладём никогда**):
```bash
mkdir -p data && tar -xzf ~/Downloads/handout.gz --strip-components=1 -C data --exclude='._*' && ls data
```
4. Свой бесплатный ключ Gemini (нужен для разметки): https://aistudio.google.com/apikey → Create API key.
   У каждого участника свой ключ — так у нас отдельные лимиты бесплатного тарифа. Ключ кладём только в `.env`:
```bash
printf 'LLM_PROVIDER=gemini\nLLM_MODEL=gemini-2.5-flash-lite\nLLM_API_KEY=ВСТАВЬ_КЛЮЧ\n' > .env && git status --short
```
   (`git status` должен вывести пустоту — `.env` скрыт от git.)
5. Запусти Claude Code в корне репозитория: `claude` — и вставь промпт ниже целиком.

## Шаг 2. Промпт для Claude (скопируй всё ниже линии)

---

Ты — мой напарник-разработчик на хакатоне Decentrathon 6.0. Я — **Infrastructure / QA** в команде из 4 человек.
Мы в корне репозитория https://github.com/torgyndoshybekova-ops/support-copilot. Окружение `.venv` (Python 3.12) активно,
данные кейса в `data/` (git-ignored), ключ LLM в `.env` (git-ignored).

Сначала прочитай `CLAUDE.md`, затем `docs/TEAM.md` (раздел «Participant 4 — Infra / QA»), `docs/API.md`, `docs/EDA.md`,
`contract/schemas.py`, `data/README.md`, а также `service/app/pipeline.py` и `service/app/llm/provider.py` — чтобы понимать,
как вызывать пайплайн и как устроен LLM-провайдер. Правила из CLAUDE.md обязательны.

Контекст: пайплайн Tech Lead уже работает — `service.app.pipeline.triage_with_meta(req)` возвращает `(TriageResponse, meta)`,
где в `meta` есть `latency_s`, `tokens_in`, `tokens_out`, `cost_usd`, `llm_ok`. `LLM_PROVIDER=none` — режим без LLM
(валидный ответ «нужен человек»), удобен для проверки обвязки без расхода лимитов.
Важно про лимиты: бесплатный Gemini — примерно 15 запросов/мин и около 1000 в день на ключ. Полный прогон 1000 тикетов
валидации с LLM — дорогое событие, делаем его осознанно, с паузами и кэшем.

**Задача 1 (ветка `feat/infra-make`)**:
- `Makefile`: `make data ARCHIVE=<путь к .gz>` (распаковать в `data/` без файлов `._*`), `make install`, `make run`
  (uvicorn), `make predict IN=... OUT=...`, `make eval`, `make test`, `make up`;
- `eval/predict.py` — батч через `triage_with_meta`: по строке на тикет, тот же порядок,
  `{"ticket_id": ..., ...TriageResponse}`; параметры `--limit`, `--sleep`; второй файл `*.meta.jsonl` с метриками;
- `Dockerfile` (сервис) и `docker-compose.yml` (сервис + admin-ui), `.env.example` с пустыми значениями;
- `ml/requirements.txt`.
Проверь: `LLM_PROVIDER=none make predict IN=data/tickets_valid.jsonl OUT=/tmp/pred.jsonl` → 1000 валидных строк. PR.

**Задача 2 (ветка `feat/infra-labeling`)** — у нас **нет разметки категорий и приоритетов** (ни в train, ни в valid):
- `ml/labeling/label.py`: LLM-разметка category/priority по критериям `data/taxonomy.json` (используй
  `service.app.llm.provider.complete_json`), батчи по 10–20 тикетов в одном запросе (экономия лимита), кэш ответов на диск,
  паузы, подсчёт стоимости. Цель: ~3000 тикетов train (стратифицированно по kind и lang) → `data/labels/train_llm.jsonl`;
- инструмент ручной проверки (простой CLI): показывает тикет + предложенную метку, принимаешь/исправляешь →
  `data/gold.jsonl`. Цель: **~200 тикетов из `tickets_valid.jsonl`**, вручную проверенных — это наш честный тестовый набор.
Всё из `data/` — только локально, не в git.

**Задача 3 (ветка `feat/infra-eval`)** — `eval/eval.py`, одна команда `make eval` печатает таблицу:
accuracy категории и macro-F1 приоритета на `data/gold.jsonl`; доля невалидных цитат (через
`service.app.rag.rules_index.load_rules()`); доля `needs_human`; нарушения обязательных правил (платёж/взлом/апелляция
без `needs_human`); p50/p95 латентности; средняя стоимость на тикет. Плюс `docs/REPORT.md` — шаблон отчёта,
цифры туда только из `make eval`.

Не трогай `contract/`, `service/app/` (только читать и вызывать), `admin-ui/`. Нужно изменение — скажи мне.
После каждой задачи: `git fetch origin && git rebase origin/main`, пуш ветки, PR, и кратко отчитайся: что сделано,
что проверено (с цифрами), что дальше.
