PYTHON ?= .venv/bin/python
CHECK_PATHS := \
	backend/app.py \
	backend/config.py \
	backend/decision \
	backend/diagnosis \
	backend/pipeline/attribution.py \
	backend/pipeline/corpus.py \
	backend/pipeline/engines/claude_sim.py \
	backend/pipeline/intents.py \
	backend/pipeline/runner.py \
	backend/storage/db.py \
	backend/taxonomy \
	contracts \
	deploy/bootstrap_cicd.py \
	demo \
	scripts \
	tests
FORMAT_PATHS := backend/taxonomy contracts/check_contract.py deploy/bootstrap_cicd.py demo scripts tests

.PHONY: bootstrap lint format format-check contract demo deploy-check test check bedrock frontend

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

demo:
	$(PYTHON) demo/validate_demo_data.py

deploy-check:
	bash -n deploy/ssm_deploy.sh
	$(PYTHON) deploy/bootstrap_cicd.py --help >/dev/null
	$(PYTHON) -m py_compile deploy/bootstrap_cicd.py deploy/deploy_aws.py deploy/deploy_ec2.py

test:
	$(PYTHON) -m pytest

check: lint format-check contract demo deploy-check test

frontend:
	npm --prefix frontend-simulator ci
	npm --prefix frontend-simulator run build
	npm --prefix frontend-diagnosis ci
	npm --prefix frontend-diagnosis run build

bedrock:
	$(PYTHON) scripts/check_bedrock.py
