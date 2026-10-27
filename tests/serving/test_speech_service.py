"""Offline speech HTTP boundary tests; model runtimes are never imported."""

import asyncio

import httpx
from pydantic import SecretStr
from roma.core.ai_contracts import STTResult, TTSResult
from roma.core.serving_settings import SpeechServingSettings
from roma.serving.app import create_speech_app

KEY = "speech-only-test-key-0123456789abcdef"
AUTH = {"Authorization": f"Bearer {KEY}"}


def settings(**kwargs):
    return SpeechServingSettings(_env_file=None, speech_service_key=SecretStr(KEY), **kwargs)


class STT:
    async def transcribe(self, request):
        return STTResult("hello", "en", turn_epoch=request.turn_epoch)


class TTS:
    async def synthesize(self, request):
        return TTSResult(b"\0\0" * 160, request.format, 20, request.turn_epoch)


def test_readiness_requires_successful_lifespan_warmup_and_private_auth():
    async def run():
        warmed = []

        async def warmup():
            warmed.append(True)

        app = create_speech_app(settings(), stt=STT(), tts=TTS(), warmup=warmup)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client:
            assert (await client.get("/health")).status_code == 401
            assert (await client.get("/ready", headers=AUTH)).status_code == 503
            async with app.router.lifespan_context(app):
                assert warmed == [True]
                assert (await client.get("/ready", headers=AUTH)).json()["ready"] is True
            assert (await client.get("/ready", headers=AUTH)).status_code == 503

    asyncio.run(run())


def test_http_clients_preserve_current_contracts_and_epoch():
    from roma.core.ai_contracts import AudioFormat, STTRequest, TTSRequest
    from roma.providers.ai.speech_http import HTTPSTTProvider, HTTPTTSProvider

    async def run():
        async def warmup():
            pass

        app = create_speech_app(settings(), stt=STT(), tts=TTS(), warmup=warmup)
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as client,
        ):
            stt = HTTPSTTProvider(settings(), client=client)
            tts = HTTPTTSProvider(settings(), client=client)
            result = await stt.transcribe(
                STTRequest(b"\0\0" * 320, AudioFormat(16000), "en", turn_epoch=7)
            )
            assert (result.text, result.language, result.turn_epoch) == ("hello", "en", 7)
            audio = await tts.synthesize(TTSRequest("नमस्ते", turn_epoch=8))
            assert (audio.format, audio.audio, audio.turn_epoch) == (
                AudioFormat(8000),
                b"\0\0" * 160,
                8,
            )
            await stt.aclose()
            assert not client.is_closed  # The injector retains HTTP session ownership.

    asyncio.run(run())


def test_service_rejects_oversized_malformed_and_unbounded_requests_before_inference():
    import base64

    class NeverSTT:
        async def transcribe(self, request):
            raise AssertionError("invalid request reached inference")

    async def run():
        async def warmup():
            pass

        app = create_speech_app(
            settings(speech_service_max_body_bytes=1024),
            stt=NeverSTT(),
            tts=TTS(),
            warmup=warmup,
        )
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
            ) as client,
        ):
            response = await client.post("/v1/stt", headers=AUTH, content=b"x" * 1025)
            assert response.status_code == 413
            assert "x" * 30 not in response.text
            payload = {
                "audio_b64": base64.b64encode(b"\0\0" * 40).decode(),
                "format": {"sample_rate_hz": 16000},
            }
            for bad in [
                dict(payload, secret="do-not-echo-me"),
                dict(payload, audio_b64="####"),
                dict(payload, turn_epoch=True),
                dict(payload, format={"sample_rate_hz": 16000, "channels": True}),
                dict(payload, metadata={"x": "a" * 257}),
            ]:
                response = await client.post("/v1/stt", headers=AUTH, json=bad)
                assert response.status_code == 422
                assert "do-not-echo-me" not in response.text
            assert (
                await client.post(
                    "/v1/stt", headers={**AUTH, "Content-Encoding": "gzip"}, content=b"x"
                )
            ).status_code == 415

    asyncio.run(run())


def test_timeout_and_cancellation_keep_admission_until_provider_work_exits():
    import base64

    import pytest

    async def scenario(cancel):
        entered, release = asyncio.Event(), asyncio.Event()

        class SlowSTT:
            async def transcribe(self, request):
                entered.set()
                await release.wait()
                return STTResult("late", "en")

        async def warmup():
            pass

        app = create_speech_app(
            settings(speech_service_timeout_secs=0.05), stt=SlowSTT(), tts=TTS(), warmup=warmup
        )
        payload = {
            "audio_b64": base64.b64encode(b"\0\0" * 40).decode(),
            "format": {"sample_rate_hz": 16000},
        }
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
            ) as client,
        ):
            pending = asyncio.create_task(client.post("/v1/stt", headers=AUTH, json=payload))
            await entered.wait()
            if cancel:
                pending.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await pending
            else:
                assert (await asyncio.wait_for(pending, 0.5)).status_code == 504
            assert (await client.post("/v1/stt", headers=AUTH, json=payload)).status_code == 429
            assert (await client.get("/health", headers=AUTH)).json()["inflight"] == 1
            release.set()
            for _ in range(20):
                if (await client.get("/health", headers=AUTH)).json()["inflight"] == 0:
                    break
                await asyncio.sleep(0)
            assert (await client.post("/v1/stt", headers=AUTH, json=payload)).status_code == 200

    asyncio.run(scenario(False))
    asyncio.run(scenario(True))


def test_batch_accepts_at_most_two_complete_requests_and_serializes_without_slicing():
    import base64

    async def run():
        seen = []

        class RecordingSTT:
            async def transcribe(self, request):
                seen.append(request.audio)
                return STTResult(str(len(request.audio)), "hi")

        async def warmup():
            pass

        app = create_speech_app(settings(), stt=RecordingSTT(), tts=TTS(), warmup=warmup)
        a, b = b"\1\0" * 100, b"\2\0" * 200
        rows = [
            {"audio_b64": base64.b64encode(audio).decode(), "format": {"sample_rate_hz": 16000}}
            for audio in (a, b)
        ]
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
            ) as client,
        ):
            response = await client.post("/v1/stt/batch", headers=AUTH, json={"requests": rows})
            assert response.status_code == 200
            assert [r["text"] for r in response.json()["results"]] == ["200", "400"]
            assert seen == [a, b]
            for bad in [[], rows + rows[:1]]:
                assert (
                    await client.post("/v1/stt/batch", headers=AUTH, json={"requests": bad})
                ).status_code == 422
            assert seen == [a, b]

    asyncio.run(run())


def test_failed_startup_never_admits_speech_and_closes_loaded_provider():
    import pytest

    async def run():
        closed = []

        class RetainedTTS(TTS):
            async def aclose(self):
                closed.append(True)

        async def warmup():
            raise RuntimeError("fixture warmup failure")

        app = create_speech_app(settings(), stt=STT(), tts=RetainedTTS(), warmup=warmup)
        with pytest.raises(RuntimeError, match="warmup failure"):
            async with app.router.lifespan_context(app):
                raise AssertionError("failed warmup admitted application")
        assert closed == [True]
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client:
            assert (await client.post("/v1/stt", headers=AUTH, json={})).status_code == 503

    asyncio.run(run())


def test_native_stt_timeout_keeps_shared_admission_until_worker_exits():
    import base64
    import threading

    from roma.providers.ai.stt import _LocalSTT

    async def run():
        entered, release, exited = threading.Event(), threading.Event(), threading.Event()

        def infer(audio, language):
            entered.set()
            try:
                release.wait(5)
                return STTResult("late", "en")
            finally:
                exited.set()

        class NativeSTT(_LocalSTT):
            async def transcribe(self, request):
                return await self._run(request, None)

        async def warmup():
            pass

        provider = NativeSTT(lambda: infer, 0.02, 30)
        app = create_speech_app(
            settings(speech_service_timeout_secs=0.1),
            stt=provider,
            tts=TTS(),
            warmup=warmup,
        )
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
            ) as client,
        ):
            try:
                response = await client.post(
                    "/v1/stt",
                    headers=AUTH,
                    json={
                        "audio_b64": base64.b64encode(b"\0\0" * 40).decode(),
                        "format": {"sample_rate_hz": 16000},
                    },
                )
                assert response.status_code in (503, 504)
                assert entered.is_set() and not exited.is_set()
                assert (await client.get("/health", headers=AUTH)).json()["inflight"] == 1
                tts = {"text": "नमस्ते", "format": {"sample_rate_hz": 8000}}
                assert (await client.post("/v1/tts", headers=AUTH, json=tts)).status_code == 429
                release.set()
                for _ in range(100):
                    if (await client.get("/health", headers=AUTH)).json()["inflight"] == 0:
                        break
                    await asyncio.sleep(0.01)
                assert exited.is_set()
                assert (await client.post("/v1/tts", headers=AUTH, json=tts)).status_code == 200
            finally:
                release.set()

    asyncio.run(run())


def test_shutdown_with_native_work_is_bounded_and_withdraws_readiness():
    import base64
    import threading

    from roma.providers.ai.stt import _LocalSTT

    async def run():
        release = threading.Event()

        def infer(audio, language):
            release.wait(5)
            return STTResult("late", "en")

        class NativeSTT(_LocalSTT):
            async def transcribe(self, request):
                return await self._run(request, None)

        async def warmup():
            pass

        provider = NativeSTT(lambda: infer, 0.01, 30)
        app = create_speech_app(
            settings(speech_service_timeout_secs=0.05, speech_service_shutdown_secs=0.02),
            stt=provider,
            tts=TTS(),
            warmup=warmup,
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client:
            try:
                async with asyncio.timeout(0.5), app.router.lifespan_context(app):
                    response = await client.post(
                        "/v1/stt",
                        headers=AUTH,
                        json={
                            "audio_b64": base64.b64encode(b"\0\0" * 40).decode(),
                            "format": {"sample_rate_hz": 16000},
                        },
                    )
                    assert response.status_code == 504
                assert not release.is_set()
                assert (await client.get("/ready", headers=AUTH)).status_code == 503
                assert (await client.post("/v1/stt", headers=AUTH, json={})).status_code == 503
            finally:
                release.set()
                await asyncio.wait_for(provider.wait_idle(), 0.5)

    asyncio.run(run())
