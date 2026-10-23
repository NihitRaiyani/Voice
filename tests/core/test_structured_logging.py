"""Operational logs correlate real boundaries without serializing caller data."""
import asyncio
import io
import json
import logging
from datetime import datetime
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from roma.core.logging import (
    JsonFormatter,
    LogEvent,
    RequestLogMiddleware,
    log_context,
    log_event,
)


@pytest.fixture
def logs(monkeypatch):
    monkeypatch.setattr(logging.getLogger("roma.events"), "disabled", False)
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    try:
        yield lambda: [json.loads(line) for line in stream.getvalue().splitlines()]
    finally:
        root.handlers = handlers
        root.setLevel(level)


def test_json_schema_and_privacy_allowlist(logs):
    with log_context(call_id="CALL-29391", turn_id=7, stage="pivot", language="gu"):
        log_event(LogEvent.LLM_COMPLETED, latency_ms=734)
        logging.getLogger("roma.test").error(
            "transcript=My name is Private +919876543210 /private/file token=sk-private",
            extra={"transcript": "Private", "phone": "+919876543210", "token": "sk-private"},
            exc_info=(ValueError, ValueError("secret"), None),
        )
    row, legacy = logs()
    assert row["event"] == "llm_completed"
    assert row["level"] == "INFO"
    assert row["call_id"] == "CALL-29391" and row["turn_id"] == 7
    assert row["stage"] == "pivot" and row["language"] == "gu"
    assert row["latency_ms"] == 734
    assert datetime.fromisoformat(row["timestamp"]).utcoffset().total_seconds() == 0
    assert legacy["event"] == "legacy_log"
    output = json.dumps(logs())
    for private in ("Private", "+919876543210", "/private", "sk-private", "secret"):
        assert private not in output
    assert "message" not in legacy and "exc_info" not in legacy


def test_context_resets_and_isolates_concurrent_calls(logs):
    async def call(sid):
        with log_context(call_id=sid, correlation_id=sid):
            for turn in (1, 2):
                with log_context(turn_id=turn, request_id=f"{turn:032x}"):
                    await asyncio.sleep(0)
                    log_event(LogEvent.TURN_STARTED)
    async def run():
        await asyncio.gather(call("CALL-1"), call("CALL-2"))
    asyncio.run(run())
    log_event(LogEvent.TURN_COMPLETED)
    rows = logs()
    assert {(r["call_id"], r["turn_id"]) for r in rows[:-1]} == {
        ("CALL-1", 1), ("CALL-1", 2), ("CALL-2", 1), ("CALL-2", 2)
    }
    assert all(r["correlation_id"] == r["call_id"] for r in rows[:-1])
    assert rows[-1]["call_id"] is None and rows[-1]["turn_id"] is None


@pytest.mark.parametrize("private", ["+919876543210", "My name is Amit", "/private/token", "bad\nvalue"])
def test_untrusted_context_fields_are_omitted(private, logs):
    with log_context(call_id=private, correlation_id=private, request_id=private,
                     stage=private, language=private, turn_id=private):
        log_event(LogEvent.TURN_STARTED)
    row = logs()[0]
    assert all(row[key] is None for key in
               ("call_id", "correlation_id", "request_id", "stage", "language", "turn_id"))


def test_invalid_extra_values_are_not_serialized(logs):
    log_event(LogEvent.TURN_FAILED, latency_ms=float("nan"), error_code="secret", status="private")
    row = logs()[0]
    assert row["latency_ms"] is None
    assert "error_code" not in row and "status" not in row


def test_http_correlation_response_and_context_cleanup(logs):
    app = FastAPI()
    app.add_middleware(RequestLogMiddleware)
    @app.get("/test")
    async def endpoint():
        log_event(LogEvent.TURN_STARTED)
        return {"ok": True}
    with TestClient(app) as client:
        responses = [client.get("/test?lead=private", headers={"X-Request-ID": "+919876543210"})
                     for _ in range(2)]
    ids = [response.headers["X-Request-ID"] for response in responses]
    assert len(set(ids)) == 2
    for response in responses:
        UUID(response.headers["X-Request-ID"])
        UUID(response.headers["X-Correlation-ID"])
    rows = [r for r in logs() if r["event"] == "request_completed"]
    assert [r["request_id"] for r in rows] == ids
    assert all(r["status_code"] == 200 for r in rows)
    assert "private" not in json.dumps(rows)
    log_event(LogEvent.TURN_COMPLETED)
    assert logs()[-1]["request_id"] is None


def test_websocket_logs_share_request_context(logs):
    app = FastAPI()
    app.add_middleware(RequestLogMiddleware)
    from fastapi import WebSocket
    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        await websocket.accept()
        log_event(LogEvent.TURN_STARTED)
        await websocket.close()
    with TestClient(app) as client, client.websocket_connect("/ws?lead=private"):
        pass
    rows = [r for r in logs() if r["event"] in ("turn_started", "request_completed")]
    assert len(rows) == 2
    assert rows[0]["request_id"] == rows[1]["request_id"]
    assert rows[0]["correlation_id"] == rows[1]["correlation_id"]


def test_request_failure_is_fixed_event_without_exception_details(logs):
    async def broken(scope, receive, send):
        raise RuntimeError("private transcript")
    with pytest.raises(RuntimeError):
        asyncio.run(RequestLogMiddleware(broken)({"type": "http"}, None, None))
    row = logs()[0]
    assert row["event"] == "request_failed" and row["level"] == "ERROR"
    assert "private" not in json.dumps(row)


def test_configured_uvicorn_handlers_emit_json_without_url_or_phone(logs):
    from roma.core.config import Settings
    from roma.core.logging import configure_logging
    logger = logging.getLogger("uvicorn.access")
    old_handlers, old_filters = logger.handlers[:], logger.filters[:]
    old_propagate, old_level = logger.propagate, logger.level
    stream = io.StringIO()
    logger.handlers = [logging.StreamHandler(stream)]
    logger.propagate = False
    logger.setLevel(logging.INFO)
    try:
        configure_logging(Settings(_env_file=None, sarvam_api_key="test-sarvam-key", redis_url="redis://127.0.0.1:1/0"))
        logger.info('%s - "%s %s HTTP/%s" %d', "+919876543210", "GET",
                    "/ws?lead=private", "1.1", 200)
        row = json.loads(stream.getvalue())
        assert row["event"] == "legacy_log"
        assert "private" not in stream.getvalue() and "+919876543210" not in stream.getvalue()
    finally:
        logger.handlers, logger.filters = old_handlers, old_filters
        logger.propagate, logger.level = old_propagate, old_level


def test_cancelled_request_restores_context_and_reports_cancellation(logs):
    async def cancelled(scope, receive, send):
        raise asyncio.CancelledError()
    async def run():
        with pytest.raises(asyncio.CancelledError):
            await RequestLogMiddleware(cancelled)({"type": "http"}, None, None)
        log_event(LogEvent.TURN_STARTED)
    asyncio.run(run())
    failed, outside = logs()
    assert failed["event"] == "request_failed" and failed["error_code"] == "CANCELLED"
    assert outside["request_id"] is None and outside["correlation_id"] is None


def test_exception_inside_nested_scope_restores_outer_context(logs):
    with log_context(call_id="CALL-1", turn_id=1):
        with pytest.raises(ValueError), log_context(call_id="CALL-2", turn_id=2):
            raise ValueError("private")
        log_event(LogEvent.TURN_STARTED)
    assert logs()[0]["call_id"] == "CALL-1" and logs()[0]["turn_id"] == 1


def test_configure_logging_reenables_owned_event_logger(capsys):
    from roma.core.config import Settings
    from roma.core.logging import configure_logging
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    logger = logging.getLogger("roma.events")
    disabled = logger.disabled
    try:
        logger.disabled = True
        configure_logging(Settings(_env_file=None, sarvam_api_key="test-sarvam-key",
                                   redis_url="redis://127.0.0.1:1/0"))
        log_event(LogEvent.TURN_STARTED)
        rows = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
        assert rows[-1]["event"] == "turn_started" and not logger.disabled
    finally:
        root.handlers, logger.disabled = handlers, disabled
        root.setLevel(level)


def test_queue_metric_fields_cannot_serialize_private_extras(logs):
    log_event(LogEvent.QUEUE_SUMMARY, queue_name="tokens", queue_metrics={
        "depth": 2, "dropped_newest": 1, "wait_ms": 12.5,
        "transcript": "private words", "phone": "+919876543210", "capacity": "private",
    })
    row = logs()[0]
    assert row["queue"] == {"depth": 2, "dropped_newest": 1, "wait_ms": 12.5}
    assert "private" not in json.dumps(row)
