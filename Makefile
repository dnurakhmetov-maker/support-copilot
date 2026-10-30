# One-command runs. Owner: Infra / QA.
# Works on macOS, Linux and Windows (GNU make): every recipe is one plain command, all logic lives in Python.
# Activate the virtualenv first (see README → Quick start); recipes call `python` from it.

PYTHON ?= python
IN     ?= data/tickets_valid.jsonl
OUT    ?= data/runs/pred.jsonl
GOLD   ?= data/gold.jsonl
LIMIT  ?=
SLEEP  ?=
PORT   ?= 8000

REQS := ml/requirements.txt $(wildcard service/requirements.txt)

.PHONY: help data install run predict eval test up down

help:
	@echo make data ARCHIVE=path/to/handout.gz - unpack case data into data/
	@echo make install                       - install Python deps into the active venv
	@echo make run                           - start the API on port $(PORT)
	@echo make predict IN=... OUT=...        - batch triage, optional LIMIT=N SLEEP=seconds RESUME=1
	@echo make eval                          - metrics table on the gold set
	@echo make test                          - pytest
	@echo make up / make down                - docker compose: service + admin-ui

data:
ifndef ARCHIVE
	$(error usage: make data ARCHIVE=path/to/handout.gz)
endif
	$(PYTHON) -m eval.unpack_data --archive "$(ARCHIVE)"

install:
	$(PYTHON) -m pip install $(addprefix -r ,$(REQS))

run:
	$(PYTHON) -m uvicorn service.app.main:app --port $(PORT) --reload

predict:
	$(PYTHON) -m eval.predict --in "$(IN)" --out "$(OUT)" $(if $(LIMIT),--limit $(LIMIT)) $(if $(SLEEP),--sleep $(SLEEP)) $(if $(RESUME),--resume)

eval:
ifeq ($(wildcard eval/eval.py),)
	$(error eval/eval.py is not merged yet (branch feat/infra-eval))
endif
	$(PYTHON) -m eval.eval --pred "$(OUT)" --gold "$(GOLD)"

test:
	$(PYTHON) -m pytest -q

up:
	docker compose $(if $(wildcard admin-ui/Dockerfile),--profile ui) up --build

down:
	docker compose --profile ui down
