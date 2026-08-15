PYTHON ?= .venv/bin/python
CHECK_PATHS := \
	backend/app.py \
	backend/config.py \
	backend/pipeline/attribution.py \
	backend/pipeline/corpus.py \
	backend/pipeline/engines/claude_sim.py \
	backend/pipeline/intents.py \
	backend/pipeline/runner.py \
	backend/storage/db.py \
	backend/taxonomy \
	contracts \
	scripts \
	tests
FORMAT_PATHS := backend/taxonomy contracts/check_contract.py scripts tests

.PHONY: bootstrap lint format format-check contract test check bedrock

bootstrap:
	bash scripts/bootstrap.sh

lint:
	$(PYTHON) -m ruff check $(CHECK_PATHS)

format:
	$(PYTHON) -m ruff check --fix $(CHECK_PATHS)
	$(PYTHON) -m ruff format $(FORMAT_PATHS)

format-check:
	$(PYTHON) -m ruff format --check $(FORMAT_PATHS)

contract:
	$(PYTHON) contracts/check_contract.py

test:
	$(PYTHON) -m pytest

check: lint format-check contract test

bedrock:
	$(PYTHON) scripts/check_bedrock.py
