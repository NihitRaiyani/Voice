SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help

UV ?= uv
HOST ?= 127.0.0.1
PORT ?= 8020
LLM_PROFILE ?= configs/local-llm-mac.json
NLTK_DATA ?= /private/tmp/voice-agent-tokenizer

export DATABASE_URL ?= postgresql+asyncpg://roma:roma@127.0.0.1:54329/roma
export REDIS_URL ?= redis://127.0.0.1:6380/0
export APP_ENV ?= dev
export API_TOKEN ?= local-dev-token
export PII_HASH_KEY ?= local-dev-pii-hash-key-change-before-any-shared-use
export STT_PROVIDER ?= mock
export LLM_PROVIDER ?= mock
export TTS_PROVIDER ?= mock
export EMBEDDING_PROVIDER ?= mock
export TELEPHONY_PROVIDER ?= mock
export PUBLIC_BASE_URL ?= https://example.invalid

.PHONY: local-llm-sync local-llm-mac-sync local-llm-download text-console text-console-mock language-lab help sync services-up services-down db-upgrade app-start lint type test test-ci test-provider-contracts ci pre-commit-install

help:
	@printf '%s\n' \
	  'make local-llm-sync          install optional direct Transformers runtime' \
	  'make local-llm-mac-sync      install optional Apple MLX runtime' \
	  'make local-llm-download      download pinned trained Mac model' \
	  'make text-console            run trained Qwen3-1.7B INT4 on Apple silicon' \
	  'make text-console-mock       run deterministic offline console' \
	  'make language-lab            generate 40 multilingual review cases' \
	  'make sync                    install locked dev/telephony/worker dependencies' \
	  'make services-up             start native local PostgreSQL and Redis; no Docker' \
	  'make db-upgrade              apply Alembic migrations to DATABASE_URL' \
	  'make app-start               services-up + db-upgrade + start FastAPI backend' \
	  'make lint                    run Ruff' \
	  'make type                    run mypy skeleton checks' \
	  'make test-provider-contracts run mock/provider interface tests' \
	  'make test-ci                 run bounded mocked CI unit/API checks' \
	  'make test                    run full offline pytest suite' \
	  'make ci                      lint + type + migrations + mocked CI tests' \
	  'make pre-commit-install      install local pre-commit hooks'

local-llm-sync:
	$(UV) sync --locked --extra dev --extra telephony --extra workers --extra local-llm

local-llm-mac-sync:
	$(UV) sync --locked --extra dev --extra telephony --extra workers --extra local-llm-mac

local-llm-download: local-llm-mac-sync
	$(UV) run --no-sync python scripts/download_local_llm.py --profile "$(LLM_PROFILE)" --fast

text-console:
	$(UV) run --no-sync python scripts/local_llm_console.py --profile "$(LLM_PROFILE)"

text-console-mock:
	$(UV) run --no-sync python scripts/local_llm_console.py --provider mock

language-lab:
	$(UV) run --no-sync python scripts/local_llm_language_eval.py --profile "$(LLM_PROFILE)"

sync:
	$(UV) sync --locked --extra dev --extra telephony --extra workers

services-up:
	./scripts/dev_services.sh up

services-down:
	./scripts/dev_services.sh down

db-upgrade:
	$(UV) run --no-sync alembic upgrade head

app-start: services-up db-upgrade
	$(UV) run --extra telephony uvicorn roma.main:create_app --factory --host $(HOST) --port $(PORT)

lint:
	$(UV) run --no-sync ruff check roma tests scripts

type:
	$(UV) run --no-sync mypy

test-provider-contracts:
	PYTHONDONTWRITEBYTECODE=1 $(UV) run --no-sync pytest -q -p no:cacheprovider \
		tests/providers/ai/test_provider_contracts.py \
		tests/providers/ai/test_transformers_llm.py \
		tests/providers/ai/test_mlx_llm.py \
		tests/core/test_local_llm_profile.py \
		tests/providers/telephony/test_telephony_provider_contracts.py \
		tests/core/test_config.py

test-ci: test-provider-contracts
	PYTHONDONTWRITEBYTECODE=1 $(UV) run --no-sync pytest -q -p no:cacheprovider \
		tests/api/v1/test_rest_resources.py \
		tests/architecture/test_dependency_direction.py \
		tests/core/test_migration_settings.py \
		tests/services/test_text_conversation_service.py \
		tests/eval/test_local_language.py

test:
	NLTK_DATA=$(NLTK_DATA) PYTHONDONTWRITEBYTECODE=1 \
		$(UV) run --no-sync pytest -q -p no:cacheprovider

ci: services-up lint type db-upgrade test-ci

pre-commit-install:
	$(UV) run --no-sync pre-commit install
