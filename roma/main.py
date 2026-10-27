"""Production application composition root.

Only this module assembles concrete providers, repositories, routes, and the
realtime pipeline. Lower layers remain importable without starting the server.
"""

import asyncio
from contextlib import asynccontextmanager, suppress
from hashlib import sha256

from fastapi import FastAPI
from redis.asyncio import Redis

from roma.api.v1.calls import mount_web_api
from roma.core.auth_settings import AuthSettings
from roma.core.config import get_settings, require_reachable_base_url
from roma.core.database import Database
from roma.core.field_encryption import configure_field_encryption
from roma.core.logging import RequestLogMiddleware, configure_logging
from roma.core.privacy_settings import PrivacySettings
from roma.core.rate_limit import RequestRateLimitMiddleware
from roma.core.telemetry import (
    get_telemetry,
    monitor_event_loop,
    sample_process_resources,
    tracing_runtime,
)
from roma.core.tokenizer import require_nltk_data
from roma.providers.ai.contracts import EmbeddingRequest
from roma.realtime.knowledge import LiveKnowledge
from roma.realtime.pipeline import build_media_app
from roma.repositories.postgres.booking import PostgresBookingStore
from roma.repositories.postgres.conversation_state import PostgresConversationStateStore
from roma.repositories.postgres.privacy import SQLPrivacyRepository
from roma.repositories.postgres.safety import PostgresSafetyEventStore
from roma.repositories.postgres.webhooks import WebhookRepository
from roma.repositories.redis.appointment_holds import RedisAppointmentHolds
from roma.repositories.redis.auth_rate_limit import AuthRateLimiter
from roma.repositories.redis.call_lease import RedisCallLease
from roma.repositories.redis.conversation_state import RedisCallStateStore
from roma.repositories.redis.request_rate_limit import RequestRateLimiter
from roma.repositories.redis.webhook_window import RedisWebhookWindow
from roma.services.auth_service import AuthService
from roma.services.booking_service import BookingService
from roma.services.booking_session import BookingSession
from roma.services.call_costs import CallCostRecorder
from roma.services.cost_service import CostService
from roma.services.knowledge_runtime import bind_knowledge_answers, build_knowledge_components
from roma.services.live_call_state import LiveCallStateStore
from roma.services.privacy_service import PrivacyService
from roma.services.recording_service import RecordingService
from roma.services.safety_service import UnavailableSafetyEventStore
from roma.services.supervisor import SupervisorHub
from roma.services.webhook_service import WebhookService
from roma.workers.postcall.paths import recordings_dir


def create_app() -> FastAPI:
    """Build the production backend application."""
    require_nltk_data()
    configure_logging()
    require_reachable_base_url()
    settings = get_settings()
    privacy_settings = PrivacySettings()
    configure_field_encryption(privacy_settings)
    database = (
        Database.from_settings(settings) if settings.database_url.get_secret_value() else None
    )

    redis = Redis.from_url(settings.redis_url.get_secret_value())
    auth_settings = AuthSettings()
    auth_secret = auth_settings.auth_jwt_secret
    auth = (
        AuthService(database.session_factory, auth_settings)
        if database is not None and auth_secret is not None
        else None
    )
    auth_limiter = (
        AuthRateLimiter(
            redis,
            auth_secret.get_secret_value().encode("utf-8"),
            limit=auth_settings.auth_rate_limit,
            window=auth_settings.auth_rate_window_secs,
        )
        if auth is not None and auth_secret is not None
        else None
    )

    supervisor = SupervisorHub(database.session_factory, redis) if database else None

    booking = BookingService(
        PostgresBookingStore(database.session_factory) if database else None,
        RedisAppointmentHolds(redis, ttl_seconds=settings.booking_hold_ttl_secs),
        db_timeout=settings.booking_db_timeout_secs,
        redis_timeout=settings.booking_redis_timeout_secs,
    )

    def build_call_store(call_settings):
        hot = RedisCallStateStore(call_settings.redis_url.get_secret_value())
        if database is None:
            return hot
        return LiveCallStateStore(
            hot,
            PostgresConversationStateStore(database.session_factory),
            lease_factory=lambda sid: RedisCallLease(redis, sid),
            observer=supervisor.on_state if supervisor else None,
        )

    if settings.rag_enabled and settings.embedding_provider != "local_multilingual":
        raise ValueError("Enabled runtime RAG requires configured local embeddings")
    knowledge = (
        build_knowledge_components(database.session_factory, settings) if database else None
    )

    @asynccontextmanager
    async def lifespan(_app):
        # Opt-in only: private SDK, bounded safe local metadata, no exporter.
        with tracing_runtime(
            get_telemetry(), enabled=getattr(settings, "tracing_enabled", False)
        ) as traces:
            _app.state.recent_trace_spans = traces
            try:
                async with application_resources(_app):
                    yield
            finally:
                _app.state.recent_trace_spans = None

    @asynccontextmanager
    async def application_resources(_app):
        monitor = asyncio.create_task(monitor_event_loop(get_telemetry()))
        if supervisor is not None:
            supervisor.start()
        try:
            sample_process_resources()
            if getattr(settings, "voice_speech_mode", "legacy_cloud") == "local_http":
                from roma.providers.ai.speech_http import HTTPSTTProvider

                probe = HTTPSTTProvider(settings)
                try:
                    if not await probe.ready():
                        raise RuntimeError("Local speech is not warmed and ready")
                finally:
                    await probe.aclose()
                if settings.rag_enabled:
                    from roma.providers.ai.speech_http import HTTPTTSProvider
                    from roma.realtime.local_speech import prime_local_deferral

                    voice = HTTPTTSProvider(settings)
                    try:
                        await prime_local_deferral(_app.state.phrase_cache, voice)
                    finally:
                        await voice.aclose()
            if knowledge is not None and settings.rag_enabled:
                # Cache-only readiness finishes before callers arrive; no paid generation.
                await knowledge.provider.embed(EmbeddingRequest(("readiness",), kind="query"))
            yield
        finally:
            if supervisor is not None:
                await supervisor.aclose()
            monitor.cancel()
            with suppress(asyncio.CancelledError):
                await monitor
            sample_process_resources()
            try:
                if auth is not None:
                    await auth.aclose()
            finally:
                try:
                    await redis.aclose()
                finally:
                    if database is not None:
                        await database.close()

    service = (
        WebhookService(
            WebhookRepository(database.session_factory), window=RedisWebhookWindow(redis)
        )
        if database
        else None
    )
    app = build_media_app(
        auto_hang_up=True,
        build_store_fn=build_call_store,
        webhook_service=service,
        lifespan=lifespan,
        booking_factory=lambda state: BookingSession(booking, state),
        safety_store=PostgresSafetyEventStore(database.session_factory, source="voice")
        if database
        else UnavailableSafetyEventStore(),
    )
    rate_secret = settings.pii_hash_key.get_secret_value().encode()
    app.state.request_rate_limiter = (
        RequestRateLimiter(redis, sha256(b"roma-rate-limit-v1:" + rate_secret).digest())
        if len(rate_secret) >= 32
        else None
    )
    app.add_middleware(RequestRateLimitMiddleware)
    app.add_middleware(RequestLogMiddleware)
    app.state.supervisor_hub = supervisor
    app.state.telemetry = get_telemetry()
    app.state.cost_service = CostService(database.session_factory) if database else None
    app.state.call_cost_factory = (
        (
            lambda state: CallCostRecorder(
                database.session_factory, app.state.cost_service, state
            )
        )
        if database
        else None
    )
    app.state.knowledge_service = knowledge.catalog if knowledge else None
    app.state.knowledge_search_service = (
        knowledge.search if knowledge and settings.rag_enabled else None
    )
    app.state.knowledge_answer_factory = (
        (
            lambda call_settings, llm, safety: LiveKnowledge(
                bind_knowledge_answers(
                    database.session_factory,
                    knowledge,
                    llm,
                    safety=safety,
                    timeout_secs=call_settings.rag_generation_timeout_secs,
                ),
                timeout_secs=call_settings.voice_rag_timeout_secs,
                filler_after_secs=call_settings.voice_rag_filler_after_secs,
            )
        )
        if knowledge and settings.rag_enabled
        else None
    )

    app.state.privacy_settings = privacy_settings
    privacy_repository = SQLPrivacyRepository(database.session_factory) if database else None
    app.state.recording_service = (
        RecordingService(privacy_repository, recordings_dir(settings), privacy_settings)
        if privacy_repository
        else None
    )
    app.state.privacy_service = (
        PrivacyService(privacy_repository, settings.roma_data_dir, redis=redis)
        if privacy_repository
        else None
    )
    app.state.booking_service = booking
    app.state.auth_settings = auth_settings
    app.state.auth_service = auth
    app.state.auth_rate_limiter = auth_limiter
    mount_web_api(app, session_factory=database.session_factory if database else None)
    return app


__all__ = ["create_app"]
