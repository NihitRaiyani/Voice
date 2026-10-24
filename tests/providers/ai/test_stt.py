from __future__ import annotations

import asyncio
import base64
import io
import json
import sys
import threading
import time
import wave
from types import SimpleNamespace

import httpx
import pytest
from roma.providers.ai.contracts import AudioFormat, ProviderUnavailable, STTRequest, STTResult
from roma.providers.ai.stt import (
    HuggingFaceSTT,
    IndicConformerSTT,
    WhisperSTT,
    normalize_transcript,
)
from roma.providers.ai.whisper_mlx import WhisperMLXSTT


def request(*, audio=b"\x00\x00" * 160, language=None, format=None):
    return STTRequest(audio=audio, format=format or AudioFormat(16000), language_hint=language)


def test_normalization_preserves_combining_marks_english_terms_and_indic_words():
    assert (
        normalize_transcript("  SEO \n Google Ads  Digital Marketing  वर्ष २०२६  વર્ષ ૨૦૨૬ ")
        == "SEO Google Ads Digital Marketing वर्ष 2026 વર્ષ 2026"
    )
    assert normalize_transcript("हिन्दी ગુજરાતી") == "हिन्दी ગુજરાતી"


@pytest.mark.parametrize("provider_type", [IndicConformerSTT, WhisperSTT])
def test_loader_is_lazy_reused_and_hints_are_canonical(provider_type):
    loads = []
    calls = []

    def load():
        loads.append(True)

        def infer(audio, language):
            calls.append((audio, language))
            return STTResult("  Google Ads  ૨૦૨૬ ", language, 0.99)

        return infer

    provider = provider_type(loader=load)
    assert not loads

    async def scenario():
        first = await provider.transcribe(request(language="gu-IN"))
        second = await provider.transcribe(request(language="HI_in"))
        assert first == STTResult("Google Ads 2026", "gu")
        assert second.language == "hi"

    asyncio.run(scenario())
    assert len(loads) == 1
    assert [language for _, language in calls] == ["gu", "hi"]


@pytest.mark.parametrize(
    "bad",
    [
        request(audio=b""),
        request(audio=b"x"),
        request(audio=b"\x00\x00" * 16001),
        request(format=AudioFormat(8000)),
        request(format=AudioFormat(16000, 2)),
        request(format=AudioFormat(16000, 1, "float32")),
        request(language="fr"),
        request(language="hi-US"),
        request(language="hi-injected"),
    ],
)
def test_invalid_requests_fail_before_loader(bad):
    def fail():
        pytest.fail("invalid request reached loader")

    provider = IndicConformerSTT(loader=fail, max_audio_secs=1)
    with pytest.raises(ValueError):
        asyncio.run(provider.transcribe(bad))


def test_indic_never_silently_treats_english_as_hindi():
    with pytest.raises(ValueError, match="English requires"):
        asyncio.run(IndicConformerSTT().transcribe(request(language="en-IN")))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"timeout_secs": 0},
        {"timeout_secs": float("nan")},
        {"max_audio_secs": float("inf")},
        {"max_audio_secs": 301},
    ],
)
def test_limits_are_finite(kwargs):
    with pytest.raises(ValueError):
        WhisperSTT(**kwargs)


@pytest.mark.parametrize(
    "failure",
    [
        RuntimeError("hf_private token audio text"),
        ProviderUnavailable("hf_private token audio text"),
    ],
)
def test_loader_errors_are_sanitized(failure):
    def load():
        raise failure

    with pytest.raises(ProviderUnavailable) as exc:
        asyncio.run(WhisperSTT(loader=load).transcribe(request()))
    assert "hf_private" not in str(exc.value)
    assert exc.value.__cause__ is None


def test_missing_indic_trust_pin_and_offline_configuration_is_actionable(monkeypatch):
    with pytest.raises(ProviderUnavailable, match="immutable"):
        asyncio.run(IndicConformerSTT().transcribe(request()))
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    with pytest.raises(ProviderUnavailable, match="HF_HUB_OFFLINE=1"):
        asyncio.run(
            IndicConformerSTT(trust_remote_code=True, revision="a" * 40).transcribe(request())
        )


@pytest.mark.parametrize("cancel", [False, True])
@pytest.mark.parametrize("provider_type", [WhisperSTT, WhisperMLXSTT])
def test_cancel_or_timeout_keeps_native_worker_busy_until_it_finishes(cancel, provider_type):
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    calls = []

    def load():
        def infer(audio, language):
            calls.append(True)
            started.set()
            release.wait(2)
            finished.set()
            return STTResult("late result", language)

        return infer

    provider = provider_type(loader=load, timeout_secs=0.04)

    async def scenario():
        task = asyncio.create_task(provider.transcribe(request()))
        assert await asyncio.to_thread(started.wait, 0.5)
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(ProviderUnavailable, match="timed out"):
                await task
        with pytest.raises(ProviderUnavailable, match="busy"):
            await provider.transcribe(request())
        assert len(calls) == 1
        release.set()
        for _ in range(100):
            if finished.is_set() and provider._slot.acquire(blocking=False):
                provider._slot.release()
                break
            await asyncio.sleep(0.001)
        else:
            pytest.fail("native worker did not release its capacity")
        assert (await provider.transcribe(request())).text == "late result"

    try:
        asyncio.run(scenario())
    finally:
        release.set()


def test_daemon_work_does_not_block_event_loop():
    def load():
        def infer(audio, language):
            time.sleep(0.03)
            return STTResult("ok")

        return infer

    async def scenario():
        task = asyncio.create_task(WhisperSTT(loader=load).transcribe(request()))
        await asyncio.sleep(0.005)
        assert not task.done()
        assert (await task).text == "ok"

    asyncio.run(scenario())


def test_whisper_real_sdk_mapping_is_cache_only_and_consumes_generator_in_worker(
    monkeypatch, tmp_path
):
    for name in ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt"):
        (tmp_path / name).write_text("{}")
    calls = []

    class Model:
        def __init__(self, path, **kwargs):
            calls.append((path, kwargs))

        def transcribe(self, samples, **kwargs):
            calls.append((samples, kwargs))

            def generate():
                assert threading.current_thread().name == "roma-stt"
                yield SimpleNamespace(text="SEO")
                yield SimpleNamespace(text="Google Ads")

            return generate(), SimpleNamespace(language="en", language_probability=0.8)

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=Model))
    monkeypatch.setitem(
        sys.modules,
        "faster_whisper.utils",
        SimpleNamespace(download_model=lambda *a, **k: pytest.fail("unexpected cache lookup")),
    )
    provider = WhisperSTT(model_id=str(tmp_path))
    result = asyncio.run(provider.transcribe(request(audio=b"\x00\x80\xff\x7f", language="en")))
    assert result == STTResult("SEO Google Ads", "en")
    assert calls[0][1]["local_files_only"] is True
    assert calls[0][1]["num_workers"] == 1
    assert calls[1][0].tolist() == [-1.0, 32767 / 32768]
    assert calls[1][1]["task"] == "transcribe"
    assert calls[1][1]["vad_filter"] is False


def endpoint(**kwargs):
    return HuggingFaceSTT(
        endpoint_url="https://asr.example.test/transcribe", token="hf_test", **kwargs
    )


def test_endpoint_custom_handler_contract_contains_wav_language_and_decoder():
    def handler(req):
        assert req.headers["Authorization"] == "Bearer hf_test"
        payload = json.loads(req.content)["inputs"]
        assert payload["language"] == "gu"
        assert payload["decoder"] == "ctc"
        with wave.open(io.BytesIO(base64.b64decode(payload["audio"]))) as wav:
            assert (wav.getnchannels(), wav.getframerate(), wav.getsampwidth()) == (1, 16000, 2)
            assert wav.readframes(160) == request().audio
        return httpx.Response(200, json={"text": "  SEO  ૨૦૨૬ ", "language": "gu"})

    result = asyncio.run(
        endpoint(transport=httpx.MockTransport(handler)).transcribe(request(language="gu-IN"))
    )
    assert result == STTResult("SEO 2026", "gu")


@pytest.mark.parametrize(
    "url",
    [
        "http://asr.test",
        "https://user:password@asr.test",
        "https://asr.test?token=x",
        "https://asr.test/#token",
        "relative",
    ],
)
def test_endpoint_rejects_insecure_or_secret_bearing_urls(url):
    with pytest.raises(ValueError):
        HuggingFaceSTT(endpoint_url=url, token="hf_test")


@pytest.mark.parametrize("status", [302, 401, 429, 500])
def test_endpoint_does_not_follow_redirects_or_return_private_error_bodies(status):
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(
            status, headers={"Location": "https://untrusted.test"}, text="hf_private transcript"
        )

    with pytest.raises(ProviderUnavailable) as exc:
        asyncio.run(endpoint(transport=httpx.MockTransport(handler)).transcribe(request()))
    assert len(calls) == 1
    assert "hf_private" not in str(exc.value)


@pytest.mark.parametrize("body", [b"not json hf_private", b'{"text":42}', b"x" * 131073])
def test_endpoint_rejects_malformed_or_oversized_responses(body):
    transport = httpx.MockTransport(lambda req: httpx.Response(200, content=body))
    with pytest.raises(ProviderUnavailable) as exc:
        asyncio.run(endpoint(transport=transport).transcribe(request()))
    assert "hf_private" not in str(exc.value)


def test_endpoint_total_deadline_and_cancellation():
    async def handler(req):
        await asyncio.sleep(1)
        return httpx.Response(200, json={"text": "late"})

    provider = endpoint(transport=httpx.MockTransport(handler), timeout_secs=0.02)

    async def scenario():
        with pytest.raises(ProviderUnavailable):
            await provider.transcribe(request())
        task = asyncio.create_task(provider.transcribe(request()))
        await asyncio.sleep(0.001)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert provider._slot.acquire(blocking=False)
        provider._slot.release()

    asyncio.run(scenario())


def test_indic_real_sdk_mapping_is_offline_pinned_and_mono_tensor(monkeypatch, tmp_path):
    calls = []
    revision = "a" * 40
    snapshot = tmp_path / "snapshots" / revision
    snapshot.mkdir(parents=True)

    class Tensor:
        def __init__(self, samples):
            self.samples = list(samples)

        def unsqueeze(self, dimension):
            assert dimension == 0
            return self

        def __truediv__(self, divisor):
            self.samples = [value / divisor for value in self.samples]
            return self

    class Context:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    def model(waveform, language, decoder):
        calls.append((waveform.samples, language, decoder))
        return "ગુજરાતી ૨૦૨૬"

    def snapshot_download(repo_id, **kwargs):
        calls.append(("snapshot", repo_id, kwargs))
        return str(snapshot)

    def config_from_pretrained(path, **kwargs):
        calls.append(("config", path, kwargs))
        return SimpleNamespace(ts_folder="untrusted-default")

    def model_from_config(config, **kwargs):
        calls.append(("model", config.ts_folder, kwargs))
        return model

    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setitem(
        sys.modules,
        "huggingface_hub",
        SimpleNamespace(
            constants=SimpleNamespace(HF_HUB_OFFLINE=True),
            snapshot_download=snapshot_download,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(
            tensor=lambda samples, **k: Tensor(samples),
            float32="float32",
            inference_mode=Context,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "transformers",
        SimpleNamespace(
            AutoConfig=SimpleNamespace(from_pretrained=config_from_pretrained),
            AutoModel=SimpleNamespace(from_config=model_from_config),
        ),
    )
    provider = IndicConformerSTT(revision=revision, trust_remote_code=True, decoder="rnnt")
    result = asyncio.run(provider.transcribe(request(audio=b"\x00\x80\xff\x7f", language="gu")))
    assert result == STTResult("ગુજરાતી 2026", "gu")
    assert calls[0] == (
        "snapshot",
        "ai4bharat/indic-conformer-600m-multilingual",
        {"revision": revision, "local_files_only": True},
    )
    assert calls[1] == (
        "config",
        str(snapshot),
        {
            "trust_remote_code": True,
            "local_files_only": True,
        },
    )
    assert calls[2] == (
        "model",
        str(snapshot),
        {
        "trust_remote_code": True,
        },
    )
    assert calls[3] == ([-1.0, 32767 / 32768], "gu", "rnnt")


def test_indic_refuses_sdk_already_imported_before_offline_setting(monkeypatch):
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setitem(
        sys.modules,
        "huggingface_hub",
        SimpleNamespace(constants=SimpleNamespace(HF_HUB_OFFLINE=False)),
    )
    with pytest.raises(ProviderUnavailable, match="Restart Python"):
        asyncio.run(
            IndicConformerSTT(revision="a" * 40, trust_remote_code=True).transcribe(request())
        )


def test_endpoint_does_not_expose_transport_exception_text():
    def handler(req):
        raise ProviderUnavailable("hf_private transcript")

    with pytest.raises(ProviderUnavailable) as exc:
        asyncio.run(endpoint(transport=httpx.MockTransport(handler)).transcribe(request()))
    assert "hf_private" not in str(exc.value)


@pytest.mark.parametrize("provider_type", [IndicConformerSTT, WhisperSTT])
@pytest.mark.parametrize(
    "format",
    [
        AudioFormat(16000.0),
        AudioFormat(16000, True),
        AudioFormat(16000, 1.0),
    ],
)
def test_noninteger_format_metadata_never_loads_or_infers(provider_type, format):
    loads = []

    def loader():
        loads.append(True)
        return lambda audio, language: STTResult("unexpected inference", language)

    provider = provider_type(loader=loader)
    with pytest.raises(ValueError, match="16000"):
        asyncio.run(provider.transcribe(request(format=format)))
    assert not loads


def test_normalization_rejects_canonical_expansion_beyond_output_limit():
    # U+0344 canonically decomposes into two combining marks even under NFC.
    expanded = "\u0344" * 20000
    assert len(expanded) < 32768
    with pytest.raises(ValueError, match="bounded"):
        normalize_transcript(expanded)
    at_limit = normalize_transcript("\u0344" * 16384)
    assert len(at_limit) == 32768


def test_expanded_model_output_is_sanitized_and_optional_language_is_preserved():
    oversized = WhisperSTT(loader=lambda: lambda audio, language: STTResult("\u0344" * 20000))
    with pytest.raises(ProviderUnavailable):
        asyncio.run(oversized.transcribe(request()))
    unknown_language = WhisperSTT(
        loader=lambda: lambda audio, language: STTResult("SEO", None, 0.9)
    )
    result = asyncio.run(unknown_language.transcribe(request()))
    assert result == STTResult("SEO", None, None)


def test_whisper_missing_tokenizer_rejected_before_sdk_can_fetch_one(monkeypatch, tmp_path):
    for name in ("model.bin", "config.json"):
        (tmp_path / name).write_text("{}")
    monkeypatch.setitem(
        sys.modules,
        "faster_whisper",
        SimpleNamespace(
            WhisperModel=lambda *a, **kw: pytest.fail("SDK could fetch missing tokenizer")
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "faster_whisper.utils",
        SimpleNamespace(download_model=lambda *a, **kw: pytest.fail("unexpected lookup")),
    )
    with pytest.raises(ProviderUnavailable, match=r"tokenizer\.json"):
        asyncio.run(WhisperSTT(model_id=str(tmp_path)).transcribe(request()))


def test_expired_parent_budget_never_starts_native_stt():
    from roma.providers.reliability import ProviderFailure, provider_budget

    async def run():
        calls = []
        def load():
            calls.append(True)
            return lambda *_: STTResult("unused")
        provider = WhisperSTT(loader=load)
        with provider_budget(0.01):
            await asyncio.sleep(0.02)
            with pytest.raises(ProviderFailure, match="timeout"):
                await provider.transcribe(request())
        assert not calls
    asyncio.run(run())
