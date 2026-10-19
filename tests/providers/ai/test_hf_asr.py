from __future__ import annotations

import asyncio
import gzip
import io
import json
import wave
from dataclasses import replace

import httpx
import pytest
from roma.providers.ai import hf_asr
from roma.providers.ai.contracts import AudioFormat, ProviderUnavailable, STTRequest, STTResult
from roma.providers.ai.hf_asr import GradioIndicSTT, GradioWhisperSTT, HFHostedSTT, HFWhisperSTT

TYPES = [GradioIndicSTT, GradioWhisperSTT, HFHostedSTT, HFWhisperSTT]
PUBLIC = TYPES[:3]
SECRET = "hf_test_private_token"


def request(language=None, **changes):
    value = STTRequest(
        audio=b"\x00\x00" * 160,
        format=AudioFormat(16000),
        language_hint=language,
        metadata={"data_classification": "public_benchmark"},
    )
    return replace(value, **changes)


def provider(kind, **kwargs):
    if kind in {HFWhisperSTT, HFHostedSTT}:
        kwargs["token"] = SECRET
    return kind(**kwargs)


class Chunks(httpx.AsyncByteStream):
    def __init__(self, *chunks):
        self.chunks = chunks
        self.closed = False
        self.seen = 0

    async def __aiter__(self):
        for chunk in self.chunks:
            self.seen += 1
            yield chunk

    async def aclose(self):
        self.closed = True


class Space:
    def __init__(self, *, upload=None, submitted=None, sse=None, api_name=None):
        self.calls = []
        self.api_name = api_name
        self.upload = ["/tmp/gradio/audio.wav"] if upload is None else upload
        self.submitted = {"event_id": "event-123_abc"} if submitted is None else submitted
        self.sse = sse

    def __call__(self, req):
        self.calls.append(req)
        assert req.url.scheme == "https"
        assert req.url.host.endswith(".hf.space")
        assert "authorization" not in req.headers
        assert SECRET.encode() not in req.content
        assert req.headers["accept-encoding"] == "identity"
        if req.url.path == "/gradio_api/upload":
            assert req.method == "POST"
            assert req.headers["content-type"].startswith("multipart/form-data;")
            assert b'filename="speech.wav"' in req.content
            start = req.content.index(b"RIFF")
            wav_bytes = req.content[start:].split(b"\r\n--", 1)[0]
            with wave.open(io.BytesIO(wav_bytes)) as wav:
                assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (
                    1,
                    2,
                    16000,
                )
                assert wav.readframes(160) == request().audio
            return httpx.Response(200, json=self.upload)
        api_name = self.api_name or "transcribe"
        if req.url.path == f"/gradio_api/call/{api_name}":
            assert req.method == "POST"
            return httpx.Response(200, json=self.submitted)
        assert req.method == "GET"
        detailed = "indicconformer" not in req.url.host
        assert req.url.path == (
            "/gradio_api/queue/data"
            if detailed
            else f"/gradio_api/call/{api_name}/event-123_abc"
        )
        result = ["  SEO  वर्ष २०२६  વર્ષ ૨૦૨૬ "]
        if "indicconformer" in req.url.host:
            result.append({"decoder": "ctc"})
        if detailed and self.sse is None:
            return httpx.Response(
                200,
                content=(
                    "data: "
                    + json.dumps(
                        {
                            "msg": "process_completed",
                            "event_id": "event-123_abc",
                            "success": True,
                            "output": {"data": result},
                        }
                    )
                    + "\n\n"
                ).encode(),
            )
        body = (
            self.sse
            if self.sse is not None
            else (
                b": ping\n\nevent: heartbeat\ndata: null\n\nevent: generating\ndata: null\n\n"
                + b"event: complete\ndata: "
                + json.dumps(result).encode()
                + b"\n\n"
            )
        )
        return httpx.Response(
            200, stream=Chunks(*(body[i : i + 7] for i in range(0, len(body), 7)))
        )


@pytest.mark.parametrize("kind", PUBLIC)
@pytest.mark.parametrize("language", ["gu-IN", "HI_in", "en-IN"])
def test_public_protocol_normalization_routing_and_no_token(kind, language):
    if kind is GradioIndicSTT and language == "en-IN":
        return
    space = Space()
    result = asyncio.run(
        provider(kind, transport=httpx.MockTransport(space)).transcribe(request(language))
    )
    expected_language = (
        language[:2].lower()
        if kind is GradioIndicSTT or (kind is HFHostedSTT and language != "en-IN")
        else None
    )
    assert result == STTResult("SEO वर्ष 2026 વર્ષ 2026", expected_language)
    assert len(space.calls) == 3
    data = json.loads(space.calls[1].content)["data"]
    assert data[0] == {"path": "/tmp/gradio/audio.wav", "meta": {"_type": "gradio.FileData"}}
    assert data[1:] == (
        [expected_language, "ctc", False] if expected_language else ["transcribe"]
    )


@pytest.mark.parametrize("kind", PUBLIC)
@pytest.mark.parametrize("classification", [None, "private", "PUBLIC_BENCHMARK", ""])
def test_public_audio_requires_explicit_benchmark_declaration_before_network(
    kind, classification
):
    metadata = {} if classification is None else {"data_classification": classification}

    def fail(req):
        pytest.fail("private audio reached transport")

    adapter = provider(kind, transport=httpx.MockTransport(fail))
    with pytest.raises(ValueError, match="public_benchmark"):
        asyncio.run(adapter.transcribe(request(metadata=metadata)))


@pytest.mark.parametrize("kind", TYPES)
@pytest.mark.parametrize(
    "bad",
    [
        request(audio=b""),
        request(audio=b"x"),
        request(audio=b"\x00\x00" * 16001),
        request(format=AudioFormat(8000)),
        request(format=AudioFormat(16000, 2)),
        request(format=AudioFormat(16000, 1, "float32")),
        request(format=AudioFormat(16000.0)),
        request(format=AudioFormat(16000, True)),
        request("fr"),
        request("hi-US"),
        request("hi/../../x"),
    ],
)
def test_invalid_pcm_and_language_never_reach_network(kind, bad):
    def fail(req):
        pytest.fail("invalid input reached transport")

    with pytest.raises(ValueError):
        asyncio.run(
            provider(kind, max_audio_secs=1, transport=httpx.MockTransport(fail)).transcribe(
                bad
            )
        )


@pytest.mark.parametrize(
    "url",
    [
        "http://demo.hf.space",
        "https://demo.hf.space:443",
        "https://u:p@demo.hf.space",
        "https://demo.hf.space/path",
        "https://demo.hf.space?token=x",
        "https://demo.hf.space/#x",
        "https://demo.hf.space.evil.test",
        "https://127.0.0.1",
        "https://demo.hf.space\n",
    ],
)
@pytest.mark.parametrize("kind", PUBLIC)
def test_spaces_endpoint_is_a_fixed_https_root(kind, url):
    with pytest.raises(ValueError):
        provider(kind, endpoint_url=url)


@pytest.mark.parametrize("kind", TYPES)
@pytest.mark.parametrize(
    "kwargs", [{"timeout_secs": 0}, {"timeout_secs": float("nan")}, {"max_audio_secs": 301}]
)
def test_constructor_limits(kind, kwargs):
    with pytest.raises(ValueError):
        provider(kind, **kwargs)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"token": ""},
        {"token": "hf_\nsecret"},
        {"token": " x"},
        {"token": SECRET, "model_id": "attacker/model"},
    ],
)
def test_router_requires_explicit_safe_token_and_approved_model(kwargs):
    with pytest.raises(ValueError):
        HFWhisperSTT(**kwargs)


@pytest.mark.parametrize("model", ["openai/whisper-large-v3", "openai/whisper-large-v3-turbo"])
def test_router_only_sends_wav_and_token_to_official_endpoint(model):
    def handler(req):
        assert str(req.url) == f"https://router.huggingface.co/hf-inference/models/{model}"
        assert req.headers["authorization"] == f"Bearer {SECRET}"
        assert req.headers["content-type"] == "audio/wav"
        assert req.headers["accept-encoding"] == "identity"
        with wave.open(io.BytesIO(req.content)) as wav:
            assert wav.readframes(160) == request().audio
        return httpx.Response(200, json={"text": " SEO  २०२६ ", "language": "gu-IN"})

    result = asyncio.run(
        HFWhisperSTT(
            token=SECRET, model_id=model, transport=httpx.MockTransport(handler)
        ).transcribe(request("en"))
    )
    assert result == STTResult("SEO 2026", "gu")


@pytest.mark.parametrize("kind", TYPES)
@pytest.mark.parametrize(
    "status, message",
    [
        (301, "unavailable"),
        (401, "access denied"),
        (403, "access denied"),
        (402, "credits"),
        (429, "rate limit"),
        (503, "unavailable"),
    ],
)
def test_status_errors_never_follow_redirect_or_echo_body(kind, status, message):
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(status, headers={"location": "https://evil.test"}, text=SECRET)

    with pytest.raises(ProviderUnavailable, match=message) as exc:
        asyncio.run(
            provider(kind, transport=httpx.MockTransport(handler)).transcribe(request())
        )
    assert len(calls) == 1
    assert SECRET not in str(exc.value)


@pytest.mark.parametrize("kind", TYPES)
@pytest.mark.parametrize("body", [b"not json hf_private", b"\xff", b"x" * 131073])
def test_invalid_or_oversized_json_is_sanitized(kind, body):
    stream = Chunks(body)
    with pytest.raises(ProviderUnavailable) as exc:
        asyncio.run(
            provider(
                kind,
                transport=httpx.MockTransport(lambda req: httpx.Response(200, stream=stream)),
            ).transcribe(request())
        )
    assert "hf_private" not in str(exc.value)
    assert stream.closed


@pytest.mark.parametrize("kind", TYPES)
def test_compression_is_rejected(kind):
    compressed = gzip.compress(b'"hf_private"')
    with pytest.raises(ProviderUnavailable, match="uncompressed"):
        asyncio.run(
            provider(
                kind,
                transport=httpx.MockTransport(
                    lambda req: httpx.Response(
                        200, headers={"content-encoding": "gzip"}, stream=Chunks(compressed)
                    )
                ),
            ).transcribe(request())
        )


@pytest.mark.parametrize(
    "upload",
    [
        [],
        [42],
        ["//evil.test/audio"],
        ["https://evil.test/audio"],
        ["/a\nb"],
        ["/" + "x" * 2048],
        ["/a", "/b"],
    ],
)
def test_upload_path_validation(upload):
    space = Space(upload=upload)
    with pytest.raises(ProviderUnavailable, match="upload response"):
        asyncio.run(GradioIndicSTT(transport=httpx.MockTransport(space)).transcribe(request()))
    assert len(space.calls) == 1


@pytest.mark.parametrize(
    "submitted",
    [
        [],
        {},
        {"event_id": 42},
        {"event_id": "../private"},
        {"event_id": "x" * 129},
        {"event_id": "x\n"},
    ],
)
def test_event_id_validation(submitted):
    space = Space(submitted=submitted)
    with pytest.raises(ProviderUnavailable, match="event response"):
        asyncio.run(GradioIndicSTT(transport=httpx.MockTransport(space)).transcribe(request()))
    assert len(space.calls) == 2


@pytest.mark.parametrize(
    "body",
    [
        b"event: error\ndata: hf_private\n\n",
        b"event: complete\ndata: not-json\n\n",
        b"event: complete\ndata: [42, {}]\n\n",
        b'event: complete\ndata: ["x"]\n\n',
        b'event: complete\ndata: ["x", false]\n\n',
        b'event: complete\ndata: ["x", {}, 1]\n\n',
        b"event: heartbeat\ndata: null\n\n",
        b'event: complete\ndata: ["\xff", {}]\n\n',
        b"x" * 131073,
    ],
)
def test_sse_errors_missing_completion_invalid_shape_and_unterminated_bounds(body):
    space = Space(sse=body)
    with pytest.raises(ProviderUnavailable) as exc:
        asyncio.run(GradioIndicSTT(transport=httpx.MockTransport(space)).transcribe(request()))
    assert "hf_private" not in str(exc.value)


@pytest.mark.parametrize(
    "body", [{}, [], {"text": 42}, {"text": "x", "language": "fr"}, {"text": "x" * 32769}]
)
def test_router_invalid_result_and_transcript_bounds(body):
    with pytest.raises(ProviderUnavailable):
        asyncio.run(
            provider(
                HFWhisperSTT,
                transport=httpx.MockTransport(lambda req: httpx.Response(200, json=body)),
            ).transcribe(request())
        )


@pytest.mark.parametrize("kind", TYPES)
@pytest.mark.parametrize(
    "error", [RuntimeError(SECRET), ProviderUnavailable(SECRET), httpx.ReadTimeout(SECRET)]
)
def test_transport_errors_are_sanitized(kind, error):
    def handler(req):
        raise error

    with pytest.raises(ProviderUnavailable) as exc:
        asyncio.run(
            provider(kind, transport=httpx.MockTransport(handler)).transcribe(request())
        )
    assert SECRET not in str(exc.value)
    assert exc.value.__cause__ is None


@pytest.mark.parametrize("kind", TYPES)
@pytest.mark.parametrize("cancel", [False, True])
def test_deadline_cancellation_busy_and_reuse(kind, cancel):
    async def scenario():
        started = asyncio.Event()
        space = Space()
        blocked = True

        async def handler(req):
            if blocked:
                started.set()
                await asyncio.Event().wait()
            if kind is HFWhisperSTT:
                return httpx.Response(200, json={"text": "ok"})
            return space(req)

        adapter = provider(kind, timeout_secs=0.03, transport=httpx.MockTransport(handler))
        task = asyncio.create_task(adapter.transcribe(request()))
        await started.wait()
        with pytest.raises(ProviderUnavailable, match="busy"):
            await adapter.transcribe(request("en" if kind is HFHostedSTT else "hi"))
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(ProviderUnavailable, match="timed out"):
                await task
        blocked = False
        assert (await adapter.transcribe(request())).text

    asyncio.run(scenario())


def test_heartbeats_cannot_extend_total_transaction_deadline():
    class Heartbeats(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            while True:
                yield b"event: heartbeat\ndata: null\n\n"
                await asyncio.sleep(0.001)

        async def aclose(self):
            self.closed = True

    stream = Heartbeats()
    space = Space()

    def handler(req):
        if req.method == "GET":
            return httpx.Response(200, stream=stream)
        return space(req)

    with pytest.raises(ProviderUnavailable, match="timed out"):
        asyncio.run(
            GradioIndicSTT(
                timeout_secs=0.02, transport=httpx.MockTransport(handler)
            ).transcribe(request())
        )
    assert stream.closed


def test_constructors_do_not_open_network_or_load_models(monkeypatch):
    def fail(*args, **kwargs):
        pytest.fail("constructor created HTTP client")

    monkeypatch.setattr(hf_asr.httpx, "AsyncClient", fail)
    for kind in TYPES:
        provider(kind)


@pytest.mark.parametrize("kind", PUBLIC)
def test_sse_byte_limit_is_enforced_before_decoding_an_unterminated_line(kind):
    stream = Chunks(b"x" * 131072, b"x", b'event: complete\ndata: ["late", {}]\n\n')
    space = Space()

    def handler(req):
        if req.method == "GET":
            return httpx.Response(200, stream=stream)
        return space(req)

    with pytest.raises(ProviderUnavailable, match="exceeds the limit"):
        asyncio.run(
            provider(kind, transport=httpx.MockTransport(handler)).transcribe(request())
        )
    assert stream.closed


@pytest.mark.parametrize("kind", PUBLIC)
def test_cancel_during_sse_closes_connection_and_releases_capacity(kind):
    async def scenario():
        started = asyncio.Event()

        class Waiting(httpx.AsyncByteStream):
            closed = False

            async def __aiter__(self):
                started.set()
                yield b"event: heartbeat\ndata: null\n\n"
                await asyncio.Event().wait()

            async def aclose(self):
                self.closed = True

        stream = Waiting()
        waiting = True
        space = Space()

        def handler(req):
            if waiting and req.method == "GET":
                return httpx.Response(200, stream=stream)
            return space(req)

        adapter = provider(kind, transport=httpx.MockTransport(handler))
        task = asyncio.create_task(adapter.transcribe(request()))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert stream.closed
        waiting = False
        assert (await adapter.transcribe(request())).text

    asyncio.run(scenario())


@pytest.mark.parametrize("language", ["gu", "hi", "en"])
def test_hosted_composition_routes_configured_default(language):
    space = Space()
    result = asyncio.run(
        provider(
            HFHostedSTT, language=language, transport=httpx.MockTransport(space)
        ).transcribe(request())
    )
    assert result.language == (None if language == "en" else language)
    assert ("whisper" in space.calls[0].url.host) is (language == "en")


def test_cloud_module_can_be_constructed_without_optional_http_runtime(monkeypatch):
    import builtins
    import importlib.util

    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name == "httpx":
            raise ImportError("optional HTTP runtime absent")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    spec = importlib.util.spec_from_file_location("roma_optional_hf_asr", hf_asr.__file__)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    adapter = module.GradioIndicSTT()

    async def run():
        with pytest.raises(ProviderUnavailable, match="httpx"):
            await adapter.transcribe(request("hi"))

    asyncio.run(run())


@pytest.mark.parametrize("api_name", ["predict", "transcribe"])
def test_whisper_api_name_is_explicit_and_used_for_submit_and_sse(api_name):
    space = Space(api_name=api_name)
    adapter = GradioWhisperSTT(api_name=api_name, transport=httpx.MockTransport(space))
    assert asyncio.run(adapter.transcribe(request("gu"))).text
    assert [str(call.url.path) for call in space.calls[1:]] == [
        f"/gradio_api/call/{api_name}",
        "/gradio_api/queue/data",
    ]


@pytest.mark.parametrize(
    "api_name", ["../predict", "predict/secret", "", "unknown", "predict\n"]
)
def test_whisper_api_name_rejects_unreviewed_or_unsafe_paths(api_name):
    with pytest.raises(ValueError, match="API"):
        GradioWhisperSTT(api_name=api_name)


def test_composition_propagates_explicit_historical_whisper_api():
    space = Space(api_name="transcribe")
    adapter = HFHostedSTT(
        whisper_endpoint_url="https://hf-audio-whisper-large-v3.hf.space",
        whisper_api_name="transcribe",
        transport=httpx.MockTransport(space),
    )
    assert asyncio.run(adapter.transcribe(request("en"))).text
    assert space.calls[1].url.path == "/gradio_api/call/transcribe"


def test_selected_whisper_failure_never_retries_or_switches_endpoint():
    space = Space(sse=b"event: error\ndata: null\n\n")
    adapter = GradioWhisperSTT(transport=httpx.MockTransport(space))
    with pytest.raises(ProviderUnavailable, match="response is invalid"):
        asyncio.run(adapter.transcribe(request("gu")))
    assert len(space.calls) == 3
    assert {call.url.host for call in space.calls} == {"hf-audio-whisper-large-v3.hf.space"}


@pytest.mark.parametrize("error", [None, "private secret remote error", {"nested": "secret"}])
def test_whisper_queue_failures_never_expose_remote_error(error):
    body = {
        "msg": "process_completed",
        "event_id": "event-123_abc",
        "success": False,
        "output": {"error": error},
    }
    space = Space(sse=("data: " + json.dumps(body) + "\n\n").encode())
    adapter = GradioWhisperSTT(transport=httpx.MockTransport(space))
    with pytest.raises(ProviderUnavailable, match=r"^HF ASR community inference failed$"):
        asyncio.run(adapter.transcribe(request("gu")))
    assert len(space.calls) == 3
    payload = json.loads(space.calls[1].content)
    assert space.calls[2].url.params["session_hash"] == payload["session_hash"]
    assert len(payload["session_hash"]) == 32


def test_whisper_queue_identifies_quota_without_retry_or_exposing_body():
    body = {
        "msg": "process_completed",
        "event_id": "event-123_abc",
        "success": False,
        "output": {"error": "You have exceeded your ZeroGPU runs limit. PRIVATE suffix secret"},
    }
    space = Space(sse=("data: " + json.dumps(body) + "\n\n").encode())
    adapter = GradioWhisperSTT(transport=httpx.MockTransport(space))
    with pytest.raises(hf_asr.HFASRQuotaExceeded) as caught:
        asyncio.run(adapter.transcribe(request("hi")))
    assert str(caught.value) == "HF ASR hosted GPU quota exhausted; hosted capacity is required"
    assert len(space.calls) == 3


@pytest.mark.parametrize(
    "change",
    [
        {"event_id": "someone-elses-job"},
        {"output": None},
        {"output": {"data": ["text", "extra"]}},
        {"output": {"data": [None]}},
        {"success": 1},
    ],
)
def test_whisper_queue_rejects_mismatched_or_malformed_completion(change):
    body = {
        "msg": "process_completed",
        "event_id": "event-123_abc",
        "success": True,
        "output": {"data": ["safe transcript"]},
        **change,
    }
    space = Space(sse=("data: " + json.dumps(body) + "\n\n").encode())
    adapter = GradioWhisperSTT(transport=httpx.MockTransport(space))
    with pytest.raises(ProviderUnavailable):
        asyncio.run(adapter.transcribe(request("en")))
