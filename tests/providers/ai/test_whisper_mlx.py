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
        return {"text": "કાલે appointment. Call tomorrow.", "language": "gu", "segments": [
            {"tokens": [1], "text": "કાલે appointment. Call tomorrow.", "no_speech_prob": .01,
             "avg_logprob": -.1, "compression_ratio": 1.}]}

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


@pytest.mark.parametrize("through_wire", [False, True])
@pytest.mark.parametrize("accepted,rejected,reject_first", [
    ("हाँ बुक कर दीजिए", "नहीं रुकिए", False),
    ("at five pm", "tomorrow", True),
])
def test_partial_batch_never_reaches_final_turn_as_consent_or_incomplete_date(
        monkeypatch, tmp_path, through_wire, accepted, rejected, reject_first):
    from roma.core.turn_epoch import TurnEpoch
    from roma.domain.audio.endpointing import Utterance
    from roma.domain.conversation.state import CallState
    from roma.providers.ai.local_routing import LocalLanguageSTT
    from roma.providers.ai.mlx_batch import validated_batch_text
    from roma.services.speech_input import SpeechInputService
    from roma.serving.protocol import STTResponse, stt_response

    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setattr(whisper_mlx, "verify_mlx_artifacts", lambda path: {})
    core = SimpleNamespace(set_cache_limit=lambda n: None, clear_cache=lambda: None)
    monkeypatch.setitem(sys.modules, "mlx", SimpleNamespace(core=core))
    monkeypatch.setitem(sys.modules, "mlx.core", core)
    monkeypatch.setitem(sys.modules, "mlx_whisper", SimpleNamespace())
    monkeypatch.setattr(whisper_mlx, "turn_chunks", lambda pcm, *args: (pcm[:32000], pcm[32000:]))
    good = SimpleNamespace(text=accepted, no_speech_prob=.01, avg_logprob=-.1, compression_ratio=1.)
    bad = SimpleNamespace(text=rejected, no_speech_prob=.01, avg_logprob=-.1, compression_ratio=3.)
    rows = [bad, good] if reject_first else [good, bad]
    monkeypatch.setattr(whisper_mlx, "decode_batch", lambda *args, **kw: validated_batch_text(rows))
    provider = whisper_mlx.WhisperMLXSTT(model_id=str(tmp_path))

    class Wire:
        async def transcribe(self, request):
            result = await provider.transcribe(request)
            return STTResponse.model_validate(stt_response(result, request.turn_epoch)).contract()

    async def run():
        guard = TurnEpoch()
        epoch = guard.advance()
        speech = SpeechInputService(LocalLanguageSTT(Wire() if through_wire else provider),
                                    object(), CallState(), epoch_guard=guard)
        pcm = b"\1\0" * 32000
        final = await speech.transcribe_segment(
            Utterance(pcm, 0, 32000, 60, "silence", 32000), turn_epoch=epoch)
        assert final.text == "" and final.language is None
        assert final.error == "ASR-BATCH-REJECTED" and final.turn_epoch == epoch

    asyncio.run(run())


@pytest.mark.parametrize("segments", [
    [{"text": "unfinished affirmative", "tokens": [1] * 440, "no_speech_prob": .01, "avg_logprob": -.1, "compression_ratio": 1.}],
    [{"tokens": [1], "text": "corrupted date", "no_speech_prob": .01, "avg_logprob": -1.1, "compression_ratio": 1.}],
    [{"tokens": [1], "text": "loop loop", "no_speech_prob": .01, "avg_logprob": -.1, "compression_ratio": 3.}],
    [{"tokens": [1], "text": "silence hallucination", "no_speech_prob": .7, "avg_logprob": -.1, "compression_ratio": 1.}],
    [{"tokens": [1], "text": "unknown quality"}],
    [{"text": "missing tokens", "no_speech_prob": .01, "avg_logprob": -.1, "compression_ratio": 1.}],
    [],
    None,
    [{"tokens": [1], "text": "हाँ बुक कर दीजिए", "no_speech_prob": .01, "avg_logprob": -.1, "compression_ratio": 1.},
     {"tokens": [1], "text": "नहीं रुकिए", "no_speech_prob": .01, "avg_logprob": -.1, "compression_ratio": 3.}],
])
def test_single_sdk_segments_apply_same_complete_turn_rejection(monkeypatch, tmp_path, segments):
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setattr(whisper_mlx, "verify_mlx_artifacts", lambda path: {})
    core = SimpleNamespace(set_cache_limit=lambda n: None, clear_cache=lambda: None)
    monkeypatch.setitem(sys.modules, "mlx", SimpleNamespace(core=core))
    monkeypatch.setitem(sys.modules, "mlx.core", core)
    response = {"text": "untrusted aggregate", "language": "hi"}
    if segments is not None:
        response["segments"] = segments
    calls = []
    monkeypatch.setitem(sys.modules, "mlx_whisper", SimpleNamespace(
        transcribe=lambda *args, **kw: calls.append(True) or response))
    monkeypatch.setattr(whisper_mlx, "turn_chunks", lambda pcm, *args: (pcm,))
    result = asyncio.run(whisper_mlx.WhisperMLXSTT(model_id=str(tmp_path)).transcribe(request("hi")))
    assert result.text == "" and result.error_code == "ASR-BATCH-REJECTED"
    assert len(calls) == 1  # No retry or fallback can invent the discarded content.
