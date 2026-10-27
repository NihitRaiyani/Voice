SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help

UV ?= uv
HOST ?= 127.0.0.1
PORT ?= 8020
export NLTK_DATA ?= $(CURDIR)/var/roma/nltk_data

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

.PHONY: tts-lab speech-lab audio-lab text-console language-lab help sync services-up services-down db-upgrade app-start lint type test test-ci test-provider-contracts ci pre-commit-install nltk-data

help:
	@printf '%s\n' \
	  'make tts-lab                 synthetic TTS/cache/frames report; no model loads' \
	  'make streaming-lab          four mock calls, queue/TTFA/CPU heartbeat evidence' \
	  'make speech-lab              synthetic endpoint/STT/language report; no downloads' \
	  'make audio-lab               synthetic audio conversion/jitter report; no services' \
	  'make text-console            synthetic mock text conversation; no paid calls' \
	  'make language-lab            40 mock language cases; native review deferred' \
	  'make nltk-data               install punkt_tab once into ignored local data' \
	  'make sync                    install locked dev/telephony/worker dependencies' \
	  'make services-up             start native local PostgreSQL and Redis; no Docker' \
	  'make db-upgrade              apply Alembic migrations to DATABASE_URL' \
	  'make app-start               services-up + db-upgrade + start FastAPI backend' \
	  'make lint                    run Ruff' \
	  'make type                    run mypy skeleton checks' \
	  'make test-provider-contracts run mock/provider interface tests' \
	  'make test-ci                 run the complete explicit offline CI catalog' \
	  'make test                    run full offline pytest suite' \
	  'make ci                      lint + type + migrations + mocked CI tests' \
	  'make pre-commit-install      install local pre-commit hooks'

tts-lab:
	PYTHONDONTWRITEBYTECODE=1 $(UV) run --no-sync python scripts/tts_lab.py

speech-lab:
	PYTHONDONTWRITEBYTECODE=1 $(UV) run --no-sync python scripts/speech_lab.py

audio-lab:
	PYTHONDONTWRITEBYTECODE=1 $(UV) run --no-sync python scripts/audio_lab.py

text-console:
	$(UV) run --no-sync python scripts/text_console.py --provider mock

language-lab:
	$(UV) run --no-sync python scripts/claude_language_lab.py --provider mock

nltk-data:
	$(UV) run --no-sync python -c 'import os, socket, nltk; socket.setdefaulttimeout(30); nltk.download("punkt_tab", download_dir=os.environ["NLTK_DATA"], raise_on_error=True)'
	$(UV) run --no-sync python -c "from roma.core.tokenizer import require_nltk_data; require_nltk_data()"

sync:
	$(UV) sync --locked --extra dev --extra telephony --extra workers

services-up:
	./scripts/dev_services.sh up

services-down:
	./scripts/dev_services.sh down

db-upgrade:
	$(UV) run --no-sync alembic upgrade head

app-start: export LLM_PROVIDER = claude
app-start: services-up db-upgrade
	$(UV) run --extra telephony uvicorn roma.main:create_app --factory --host $(HOST) --port $(PORT)

lint:
	$(UV) run --no-sync ruff check roma tests scripts benchmarks/asr/hf_endpoint

type:
	$(UV) run --no-sync mypy

test-provider-contracts:
	PYTHONDONTWRITEBYTECODE=1 $(UV) run --no-sync pytest -q -p no:cacheprovider \
		tests/providers/ai/test_provider_contracts.py \
		tests/providers/ai/test_openai_llm.py \
		tests/providers/ai/test_claude_llm.py \
		tests/providers/telephony/test_telephony_provider_contracts.py \
		tests/core/test_config.py

test-ci:
	PYTHONDONTWRITEBYTECODE=1 $(UV) run --no-sync python scripts/run_ci_tests.py

test:
	NLTK_DATA=$(NLTK_DATA) PYTHONDONTWRITEBYTECODE=1 \
		$(UV) run --no-sync pytest -q -p no:cacheprovider

ci: services-up lint type db-upgrade test-ci

pre-commit-install:
	$(UV) run --no-sync pre-commit install

voice-prototype:
	$(UV) run --no-sync python scripts/voice_prototype.py $(ARGS)

.PHONY: demo-db demo-db-down
# Explicit opt-in only; these are never app-start prerequisites.
demo-db:
	$(UV) run --no-sync python scripts/demo_database.py up

demo-db-down:
	$(UV) run --no-sync python scripts/demo_database.py down


.PHONY: streaming-lab
streaming-lab:
	$(UV) run --no-sync python scripts/streaming_lab.py

.PHONY: acceptance-live
acceptance-live:
	$(UV) run --no-sync python scripts/acceptance_live.py $(ARGS)

.PHONY: rag-benchmark knowledge-worker
rag-benchmark:
	$(UV) run --no-sync python scripts/rag_benchmark.py $(ARGS)

knowledge-worker:
	$(UV) run --no-sync python -m roma.workers.knowledge --drain --allow-database-write $(ARGS)

.PHONY: evaluation-lab load-backend security-policy secret-scan stack-up stack-down stack-smoke
evaluation-lab:
	$(UV) run --no-sync python scripts/evaluate_project.py $(ARGS)

load-backend:
	$(UV) run --no-sync python scripts/load_backend.py $(ARGS)

security-policy:
	$(UV) run --no-sync python scripts/check_production_policy.py

secret-scan:
	$(UV) run --no-sync python scripts/scan_secrets.py $(ARGS)

stack-up:
	$(UV) run --no-sync python scripts/container_stack.py up $(ARGS)

stack-down:
	$(UV) run --no-sync python scripts/container_stack.py down $(ARGS)

stack-smoke:
	$(UV) run --no-sync python scripts/container_stack.py smoke $(ARGS)
