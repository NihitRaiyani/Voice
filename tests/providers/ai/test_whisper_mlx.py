import asyncio
import sys
from types import SimpleNamespace

import numpy as np
import pytest
from roma.providers.ai import whisper_mlx
from roma.providers.ai.contracts import AudioFormat, ProviderUnavailable, STTRequest, STTResult


def request(language="gu"):
    return STTRequest(b"\0\0" * 16000, AudioFormat(16000), language)


def test_mlx_loader_is_lazy_and_reused():
    loads = []

    def load():
        loads.append(True)
        return lambda audio, language: STTResult(" Google Ads ૨૦૨૬ ", language)

    provider = whisper_mlx.WhisperMLXSTT(loader=load)
    assert not loads

    async def run():
        assert (await provider.transcribe(request())).text == "Google Ads 2026"
        assert (await provider.transcribe(request("hi-IN"))).language == "hi"

    asyncio.run(run())
    assert len(loads) == 1


def test_invalid_mlx_audio_rejected_without_loading():
    provider = whisper_mlx.WhisperMLXSTT(loader=lambda: pytest.fail("loaded"))
    with pytest.raises(ValueError):
        asyncio.run(provider.transcribe(STTRequest(b"x", AudioFormat(16000), "gu")))


def test_mlx_requires_offline_verified_local_artifacts_before_sdk(monkeypatch, tmp_path):
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    with pytest.raises(ProviderUnavailable, match="HF_HUB_OFFLINE"):
        asyncio.run(whisper_mlx.WhisperMLXSTT(model_id=str(tmp_path)).transcribe(request()))
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    with pytest.raises(ProviderUnavailable, match="snapshot"):
        asyncio.run(whisper_mlx.WhisperMLXSTT(model_id=str(tmp_path)).transcribe(request()))


def test_one_mlx_decode_keeps_full_audio_uses_bounded_tokens_and_clears_cache(
    monkeypatch, tmp_path
):
    calls, cache = [], []
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setattr(whisper_mlx, "verify_mlx_artifacts", lambda path: {})
    core = SimpleNamespace(
        set_cache_limit=lambda n: cache.append(n), clear_cache=lambda: cache.append("clear")
    )
    monkeypatch.setitem(sys.modules, "mlx", SimpleNamespace(core=core))
    monkeypatch.setitem(sys.modules, "mlx.core", core)

    def transcribe(samples, **options):
        calls.append((samples, options))
        return {"text": "કાલે appointment. Call tomorrow.", "language": "gu"}

    monkeypatch.setitem(sys.modules, "mlx_whisper", SimpleNamespace(transcribe=transcribe))
    result = asyncio.run(
        whisper_mlx.WhisperMLXSTT(model_id=str(tmp_path)).transcribe(request())
    )
    assert result.text == "કાલે appointment. Call tomorrow."
    assert len(calls) == 1 and calls[0][0].shape == (16000,)
    assert calls[0][0].dtype == np.float32
    assert calls[0][1]["task"] == "transcribe"
    assert calls[0][1]["temperature"] == 0
    assert calls[0][1]["sample_len"] == 440
    assert calls[0][1]["condition_on_previous_text"] is False
    assert cache[-1] == "clear"


def test_shared_mlx_admission_rejects_another_adapter_without_queue(monkeypatch, tmp_path):
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setattr(whisper_mlx, "verify_mlx_artifacts", lambda path: {})
    core = SimpleNamespace(set_cache_limit=lambda n: None, clear_cache=lambda: None)
    monkeypatch.setitem(sys.modules, "mlx", SimpleNamespace(core=core))
    monkeypatch.setitem(sys.modules, "mlx.core", core)
    monkeypatch.setitem(
        sys.modules,
        "mlx_whisper",
        SimpleNamespace(transcribe=lambda *a, **kw: pytest.fail("queued")),
    )
    whisper_mlx._NATIVE_SLOT.acquire()
    try:
        with pytest.raises(ProviderUnavailable, match="busy"):
            asyncio.run(whisper_mlx.WhisperMLXSTT(model_id=str(tmp_path)).transcribe(request()))
    finally:
        whisper_mlx._NATIVE_SLOT.release()


def test_registry_selects_mlx_lazily_without_loading_runtime(monkeypatch):
    from roma.core.speech_settings import SpeechSettings
    from roma.providers.ai.registry import build_stt_provider

    provider = build_stt_provider(SpeechSettings(_env_file=None, stt_provider="whisper_mlx"))
    assert isinstance(provider, whisper_mlx.WhisperMLXSTT)
    assert provider._inference is None
    assert provider.model_id.endswith("whisper-large-v3-turbo-mlx-4bit")


def test_mixed_route_uses_one_batch_and_preserves_full_audio(monkeypatch, tmp_path):
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setattr(whisper_mlx, "verify_mlx_artifacts", lambda path: {})
    core = SimpleNamespace(set_cache_limit=lambda n: None, clear_cache=lambda: None)
    monkeypatch.setitem(sys.modules, "mlx", SimpleNamespace(core=core))
    monkeypatch.setitem(sys.modules, "mlx.core", core)
    monkeypatch.setitem(
        sys.modules,
        "mlx_whisper",
        SimpleNamespace(transcribe=lambda *a, **kw: pytest.fail("second ASR decode")),
    )
    calls = []
    monkeypatch.setattr(
        whisper_mlx, "turn_chunks", lambda pcm, scorer, language, policy: (pcm[:8000], pcm[8000:])
    )

    def decode(folder, parts, language, policy):
        calls.append((parts, language))
        return "કાલે appointment. Call tomorrow."

    monkeypatch.setattr(whisper_mlx, "decode_batch", decode)
    original = STTRequest(
        b"\0\0" * 16000, AudioFormat(16000), "gu", {"local_language_route": "gu-en"}
    )
    result = asyncio.run(whisper_mlx.WhisperMLXSTT(model_id=str(tmp_path)).transcribe(original))
    assert result.text.endswith("Call tomorrow.") and len(calls) == 1
    assert b"".join(calls[0][0]) == original.audio and calls[0][1] == "gu"
