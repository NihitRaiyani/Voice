import asyncio

import httpx
import pytest
from pydantic import SecretStr, ValidationError
from roma.core.ai_contracts import AudioFormat, ProviderUnavailable, STTRequest
from roma.core.serving_settings import SpeechServingSettings
from roma.providers.ai.speech_http import HTTPSTTProvider, HTTPTTSProvider
from tests.serving.test_speech_service import STT, TTS, create_speech_app, settings


def test_provider_ready_is_authenticated_bounded_and_false_until_warmed():
    async def run():
        async def warmup():
            pass

        cfg = settings()
        app = create_speech_app(cfg, stt=STT(), tts=TTS(), warmup=warmup)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as client:
            stt, tts = HTTPSTTProvider(cfg, client=client), HTTPTTSProvider(cfg, client=client)
            assert await stt.ready() is False
            async with app.router.lifespan_context(app):
                assert await stt.ready() is True
                assert await tts.ready() is True
                wrong = HTTPSTTProvider(
                    cfg.model_copy(
                        update={
                            "speech_service_key": SecretStr(
                                "different-private-key-0123456789abc"
                            )
                        }
                    ),
                    client=client,
                )
                assert await wrong.ready() is False

    asyncio.run(run())


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1:8091",
        "http://localhost:8091",
        "http://example.com:8091",
        "http://127.0.0.1:8091/path",
        "http://user:password@127.0.0.1:8091",
        "http://127.0.0.1:8091?token=x",
        "http://127.0.0.1:8091#x",
        "http://127.0.0.1",
    ],
)
def test_speech_settings_reject_remote_dns_and_credential_bearing_urls(url):
    with pytest.raises(ValidationError):
        SpeechServingSettings(_env_file=None, speech_service_url=url)


def test_service_key_is_separate_fail_closed_and_org_limits_remain_unknown():
    cfg = SpeechServingSettings(_env_file=None)
    assert (cfg.anthropic_org_rpm, cfg.anthropic_org_itpm, cfg.anthropic_org_otpm) == (
        None,
        None,
        None,
    )
    with pytest.raises(ValueError, match="separate private"):
        HTTPSTTProvider(cfg)
    assert SecretStr("private-value").get_secret_value() not in repr(cfg)


def test_http_provider_does_not_follow_redirect_retry_or_expose_error_body():
    async def run():
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(
                307,
                headers={"Location": "https://example.com/stolen"},
                text="private-upstream-error",
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), follow_redirects=True
        ) as client:
            provider = HTTPSTTProvider(settings(), client=client)
            with pytest.raises(ProviderUnavailable) as failure:
                await provider.transcribe(STTRequest(b"\0\0" * 10, AudioFormat(16000)))
            assert "private-upstream-error" not in str(failure.value)
            assert len(requests) == 1
            assert requests[0].url.host == "127.0.0.1"
            assert await provider.ready() is False
            assert len(requests) == 2

    asyncio.run(run())


@pytest.mark.parametrize(
    "reply",
    [
        {"text": "hello", "turn_epoch": 8},
        {"text": "hello", "turn_epoch": True},
        {"text": "hello", "unexpected": "private"},
        {"text": "hello", "confidence": 2.0},
    ],
)
def test_http_stt_rejects_stale_or_malformed_provider_responses(reply):
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=reply))
        ) as client:
            with pytest.raises(ProviderUnavailable):
                await HTTPSTTProvider(settings(), client=client).transcribe(
                    STTRequest(b"\0\0" * 10, AudioFormat(16000), turn_epoch=7)
                )

    asyncio.run(run())


def test_http_response_bytes_and_hanging_transport_are_bounded():
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, content=b"x" * 1025)
            )
        ) as client:
            provider = HTTPSTTProvider(
                settings(speech_service_max_body_bytes=1024), client=client
            )
            assert await provider.ready() is False

        async def hanging(request):
            await asyncio.Event().wait()

        async with httpx.AsyncClient(transport=httpx.MockTransport(hanging)) as client:
            provider = HTTPSTTProvider(
                settings(speech_service_timeout_secs=0.01), client=client
            )
            assert await asyncio.wait_for(provider.ready(), 0.5) is False
            with pytest.raises(ProviderUnavailable):
                await asyncio.wait_for(
                    provider.transcribe(STTRequest(b"\0\0" * 10, AudioFormat(16000))), 0.5
                )

    asyncio.run(run())


def test_http_tts_identity_tracks_served_artifacts_without_native_client_load():
    from roma.core.ai_contracts import TTSRequest

    async def run():
        class VersionedTTS(TTS):
            identity = "indic-tts:artifact-a:resident-v1"

            async def cache_identity(self, request):
                return self.identity

        async def warmup():
            pass

        native = VersionedTTS()
        app = create_speech_app(settings(), stt=STT(), tts=native, warmup=warmup)
        async with (app.router.lifespan_context(app),
                    httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as client):
            served = HTTPTTSProvider(settings(), client=client)
            request = TTSRequest("नमस्ते", language="hi", turn_epoch=3)
            assert await served.cache_identity(request) == native.identity
            native.identity = "indic-tts:artifact-b:resident-v1"
            assert await served.cache_identity(request) == native.identity
            native.identity = "private/artifact/path"
            with pytest.raises(ProviderUnavailable):
                await served.cache_identity(request)

    asyncio.run(run())
