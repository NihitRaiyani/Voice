SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help

UV ?= uv
HOST ?= 127.0.0.1
PORT ?= 8020
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

.PHONY: tts-lab speech-lab audio-lab text-console language-lab help sync services-up services-down db-upgrade app-start lint type test test-ci test-provider-contracts ci pre-commit-install

help:
	@printf '%s\n' \
	  'make tts-lab                 synthetic TTS/cache/frames report; no model loads' \
	  'make speech-lab              synthetic endpoint/STT/language report; no downloads' \
	  'make audio-lab               synthetic audio conversion/jitter report; no services' \
	  'make text-console            synthetic mock text conversation; no paid calls' \
	  'make language-lab            40 mock language cases; native review deferred' \
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

tts-lab:
	PYTHONDONTWRITEBYTECODE=1 $(UV) run --no-sync python scripts/tts_lab.py

speech-lab:
	PYTHONDONTWRITEBYTECODE=1 $(UV) run --no-sync python scripts/speech_lab.py

audio-lab:
	PYTHONDONTWRITEBYTECODE=1 $(UV) run --no-sync python scripts/audio_lab.py

text-console:
	$(UV) run --no-sync python scripts/text_console.py --provider mock

language-lab:
	$(UV) run --no-sync python scripts/openai_language_lab.py --provider mock

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
	$(UV) run --no-sync ruff check roma tests scripts benchmarks/asr/hf_endpoint

type:
	$(UV) run --no-sync mypy

test-provider-contracts:
	PYTHONDONTWRITEBYTECODE=1 $(UV) run --no-sync pytest -q -p no:cacheprovider \
		tests/providers/ai/test_provider_contracts.py \
		tests/providers/ai/test_openai_llm.py \
		tests/providers/telephony/test_telephony_provider_contracts.py \
		tests/core/test_config.py

test-ci: test-provider-contracts
	PYTHONDONTWRITEBYTECODE=1 $(UV) run --no-sync pytest -q -p no:cacheprovider \
		tests/domain/audio \
		tests/domain/conversation/test_language.py \
		tests/providers/ai/test_stt.py \
		tests/providers/ai/test_hf_asr.py \
		tests/eval/test_hosted_asr.py \
		tests/eval/test_asr_benchmark.py \
		tests/eval/test_benchmark_evidence.py \
		tests/eval/test_tts_setup.py \
		tests/eval/test_tts_lab.py \
		tests/eval/test_tts_safety_lab.py \
		tests/core/test_tts_settings.py \
		tests/providers/ai/test_indic_tts.py \
		tests/services/test_tts_service.py \
		tests/services/test_voice_prototype.py \
		tests/providers/audio \
		tests/repositories/postgres/test_benchmarks.py \
		tests/providers/ai/test_hf_endpoint_handler.py \
		tests/providers/ai/test_silero.py \
		tests/core/test_speech_settings.py \
		tests/services/test_speech_input.py \
		tests/eval/test_speech_lab.py \
		tests/eval/test_audio_lab.py \
		tests/providers/telephony/twilio/test_audio_validation.py \
		tests/providers/telephony/twilio/test_twilio_serializer.py \
		tests/realtime/test_ulaw.py \
		tests/realtime/test_audio_vad_boundary.py \
		tests/api/v1/test_rest_resources.py \
		tests/api/v1/test_booking_resources.py \
		tests/api/v1/test_webhook_idempotency.py \
		tests/services/test_booking_service.py \
		tests/domain/conversation/test_booking_commit.py \
		tests/realtime/test_booking_commit.py \
		tests/repositories/redis/test_appointment_holds.py \
		tests/repositories/postgres/test_booking_module.py \
		tests/architecture/test_dependency_direction.py \
		tests/core/test_migration_settings.py \
		tests/services/test_text_conversation_service.py \
		tests/services/test_safety_service.py \
		tests/domain/safety \
		tests/realtime/test_safety_audit.py \
		tests/repositories/postgres/test_safety_audit.py \
		tests/eval/test_openai_language.py \
		tests/core/test_openai_llm_settings.py

test:
	NLTK_DATA=$(NLTK_DATA) PYTHONDONTWRITEBYTECODE=1 \
		$(UV) run --no-sync pytest -q -p no:cacheprovider

ci: services-up lint type db-upgrade test-ci

pre-commit-install:
	$(UV) run --no-sync pre-commit install

voice-prototype:
	$(UV) run --no-sync python scripts/voice_prototype.py
