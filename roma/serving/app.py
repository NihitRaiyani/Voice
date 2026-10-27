"""Private loopback HTTP speech service. Importing never initializes a model."""

from __future__ import annotations

import asyncio
import hmac
import json
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from roma.core.ai_contracts import ProviderUnavailable, STTProvider, TTSProvider
from roma.core.serving_settings import SpeechServingSettings
from roma.serving.protocol import STTBatchBody, STTBody, TTSBody, stt_response, tts_response


def create_speech_app(
    settings: SpeechServingSettings,
    *,
    stt: STTProvider,
    tts: TTSProvider,
    warmup: Callable[[], Awaitable[None]],
) -> FastAPI:
    key = settings.require_key()
    ready = False
    active: set[asyncio.Task] = set()

    @asynccontextmanager
    async def lifespan(app):
        nonlocal ready
        try:
            async with asyncio.timeout(settings.speech_service_startup_secs):
                await warmup()
            ready = True
            yield
        finally:
            ready = False
            if active:
                _, pending = await asyncio.wait(
                    active, timeout=settings.speech_service_shutdown_secs
                )
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
            for provider in (stt, tts):
                wait_idle = getattr(provider, "wait_idle", None)
                if wait_idle is not None:
                    try:
                        async with asyncio.timeout(settings.speech_service_shutdown_secs):
                            await wait_idle()
                    except TimeoutError:
                        pass  # Readiness is withdrawn; native threads cannot be killed safely.
                close = getattr(provider, "aclose", None)
                if close is not None:
                    async with asyncio.timeout(settings.speech_service_shutdown_secs):
                        await close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def auth(request: Request, call_next):
        supplied = request.headers.get("authorization", "")
        if not hmac.compare_digest(supplied.encode(), ("Bearer " + key).encode()):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        request.state.speech_deadline = (
            asyncio.get_running_loop().time() + settings.speech_service_timeout_secs
        )
        return await call_next(request)

    @app.get("/health")
    async def health():
        return {
            "alive": True,
            "ready": ready,
            "inflight": len(active),
            "max_inflight": settings.speech_service_max_inflight,
            "asr_batch_mode": "serialized_requests_max_2; existing_provider_internal_chunks_unchanged",
        }

    @app.get("/ready")
    async def readiness():
        return JSONResponse({"ready": ready}, status_code=200 if ready else 503)

    async def body(request: Request):
        if not ready:
            raise HTTPException(status_code=503, detail="not_ready")
        if request.headers.get("content-encoding", "identity").lower() != "identity":
            raise HTTPException(status_code=415, detail="unsupported_encoding")
        size = request.headers.get("content-length")
        if size is not None:
            if not size.isdecimal():
                raise HTTPException(status_code=422, detail="invalid_payload")
            if int(size) > settings.speech_service_max_body_bytes:
                raise HTTPException(status_code=413, detail="payload_too_large")
        data = bytearray()
        try:
            async with asyncio.timeout(
                max(0, request.state.speech_deadline - asyncio.get_running_loop().time())
            ):
                async for chunk in request.stream():
                    if len(data) + len(chunk) > settings.speech_service_max_body_bytes:
                        raise HTTPException(status_code=413, detail="payload_too_large")
                    data.extend(chunk)
            return json.loads(data)
        except TimeoutError:
            raise HTTPException(status_code=408, detail="request_timeout") from None
        except (ValueError, UnicodeError):
            raise HTTPException(status_code=422, detail="invalid_payload") from None

    def finished(task):
        active.discard(task)
        if not task.cancelled():
            task.exception()  # Retrieve late failures; never log transcript/provider payloads.

    async def run(request: Request, operation, provider):
        if len(active) >= settings.speech_service_max_inflight:
            raise HTTPException(
                status_code=429, detail="speech_busy", headers={"Retry-After": "1"}
            )

        async def owned_operation():
            try:
                return await operation()
            finally:
                wait_idle = getattr(provider, "wait_idle", None)
                if wait_idle is not None:
                    await wait_idle()

        task = asyncio.create_task(owned_operation())
        active.add(task)
        task.add_done_callback(finished)

        async def disconnected():
            while True:
                message = await request.receive()
                if message["type"] == "http.disconnect":
                    return

        disconnect = asyncio.create_task(disconnected())
        try:
            timeout = max(0, request.state.speech_deadline - asyncio.get_running_loop().time())
            done, _ = await asyncio.wait(
                (task, disconnect), timeout=timeout, return_when=asyncio.FIRST_COMPLETED
            )
            if task in done:
                try:
                    return task.result()
                except (ProviderUnavailable, ValueError):
                    raise HTTPException(status_code=503, detail="speech_unavailable") from None
                except Exception:  # noqa: BLE001 -- fixed response, never expose SDK text.
                    raise HTTPException(status_code=503, detail="speech_unavailable") from None
            if disconnect in done:
                raise HTTPException(status_code=499, detail="caller_disconnected")
            raise HTTPException(status_code=504, detail="speech_timeout")
        finally:
            request.state.speech_withdrawn = True
            # Work remains owned in active after timeout or caller cancellation.
            disconnect.cancel()
            await asyncio.gather(disconnect, return_exceptions=True)

    @app.post("/v1/stt")
    async def transcribe(request: Request):
        data = await body(request)
        try:
            contract = STTBody.model_validate(data).contract(settings)
        except (ValueError, ValidationError):
            return JSONResponse({"error": "invalid_payload"}, status_code=422)

        async def operation():
            result = await stt.transcribe(contract)
            return stt_response(result, contract.turn_epoch)

        return await run(request, operation, stt)

    @app.post("/v1/stt/batch")
    async def transcribe_batch(request: Request):
        data = await body(request)
        try:
            contracts = [
                row.contract(settings) for row in STTBatchBody.model_validate(data).requests
            ]
        except (ValueError, ValidationError):
            return JSONResponse({"error": "invalid_payload"}, status_code=422)

        async def operation():
            # The existing provider's per-turn two-chunk optimization is retained.
            # Independent HTTP requests are deliberately serialized, not called a model batch.
            results = []
            for contract in contracts:
                if (
                    getattr(request.state, "speech_withdrawn", False)
                    or asyncio.get_running_loop().time() >= request.state.speech_deadline
                ):
                    raise ProviderUnavailable("Speech batch deadline expired")
                result = await stt.transcribe(contract)
                results.append(stt_response(result, contract.turn_epoch))
            return {"results": results}

        return await run(request, operation, stt)

    @app.post("/v1/tts")
    async def synthesize(request: Request):
        data = await body(request)
        try:
            contract = TTSBody.model_validate(data).contract(settings)
        except (ValueError, ValidationError):
            return JSONResponse({"error": "invalid_payload"}, status_code=422)

        async def operation():
            result = await tts.synthesize(contract)
            return tts_response(
                result, contract.turn_epoch, settings.speech_service_max_audio_secs
            )

        return await run(request, operation, tts)

    return app
