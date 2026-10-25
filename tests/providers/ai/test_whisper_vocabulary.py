"""Fixed vocabulary wiring and SDK decoder contracts; no model inference."""

import asyncio
import hashlib
import importlib.metadata
import importlib.util
import sys
from types import ModuleType, SimpleNamespace

import numpy as np
import pytest
from roma.core.speech_settings import SpeechSettings
from roma.providers.ai import whisper_mlx
from roma.providers.ai.contracts import AudioFormat, STTRequest


@pytest.fixture
def sdk_source(monkeypatch):
    """Load SDK Python logic against NumPy, never import native MLX or weights."""
    pytest.importorskip("tiktoken")
    try:
        root = importlib.metadata.distribution("mlx-whisper").locate_file("mlx_whisper")
    except importlib.metadata.PackageNotFoundError:
        pytest.skip("Optional pinned SDK source is unavailable")
    package = ModuleType("mlx_whisper")
    package.__path__ = [str(root)]
    monkeypatch.setitem(sys.modules, "mlx_whisper", package)

    def source(name):
        spec = importlib.util.spec_from_file_location("mlx_whisper." + name, root / (name + ".py"))
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, spec.name, module)
        spec.loader.exec_module(module)
        setattr(package, name, module)
        return module

    tokenizer = source("tokenizer")
    core = SimpleNamespace(**{name: getattr(np, name) for name in (
        "array", "zeros", "full", "stack", "concatenate", "arange", "all", "pad",
        "float16", "float32", "nan", "inf",
    )})
    core.compile = lambda fn: fn
    core.eval = lambda *a: None
    core.broadcast_to = lambda value, shape: np.broadcast_to(value, shape).copy()
    core.logsumexp = lambda value, **kw: np.logaddexp.reduce(value, **kw)
    core.softmax = lambda value, axis: np.exp(value - core.logsumexp(value, axis=axis, keepdims=True))
    monkeypatch.setitem(sys.modules, "mlx", SimpleNamespace(core=core))
    monkeypatch.setitem(sys.modules, "mlx.core", core)
    monkeypatch.setitem(sys.modules, "mlx.utils", SimpleNamespace(
        tree_map=lambda fn, values: tuple(fn(value) for value in values),
    ))
    audio = ModuleType("mlx_whisper.audio")
    audio.CHUNK_LENGTH, audio.N_SAMPLES = 30, 480000
    audio.log_mel_spectrogram = None
    audio.pad_or_trim = None
    monkeypatch.setitem(sys.modules, "mlx_whisper.audio", audio)
    package.audio = audio
    decoding = source("decoding")
    holder = SimpleNamespace(get_model=lambda *a: pytest.fail("model loading attempted"))
    transcribe = SimpleNamespace(ModelHolder=holder)
    monkeypatch.setitem(sys.modules, "mlx_whisper.transcribe", transcribe)
    return SimpleNamespace(tokenizer=tokenizer, core=core, audio=audio,
                           decoding=decoding, holder=holder)


def test_fixed_vocabulary_has_only_reviewed_nouns_and_bounded_context():
    from roma.providers.ai.whisper_vocabulary import (
        PROMPT,
        load_prompt,
        prompt_details,
    )

    assert PROMPT == (
        "Weltec Institute, Vadodara, Ahmedabad, Digital Marketing, SEO, "
        "social media marketing, Google Ads."
    )
    details = prompt_details(True)
    assert details == {
        "enabled": True,
        "version": "weltec-booking-vocabulary-v1",
        "sha256": hashlib.sha256(PROMPT.encode()).hexdigest(),
        "utf8_bytes": 98,
        "tokens": 24,
        "max_utf8_bytes": 128,
        "max_tokens": 32,
        "sample_len": 410,
    }
    assert 5 + details["max_tokens"] + details["sample_len"] < 448
    assert load_prompt(False) is None
    assert prompt_details(False) == {"enabled": False, "sample_len": 440}


@pytest.mark.parametrize("value", ["caller supplied text", 1, None, {}, []])
def test_vocabulary_api_rejects_every_non_boolean_without_runtime(value):
    from roma.providers.ai.whisper_vocabulary import load_prompt, prompt_details

    for method in (load_prompt, prompt_details):
        with pytest.raises(ValueError, match="boolean"):
            method(value)
    with pytest.raises(ValueError, match="boolean"):
        whisper_mlx.WhisperMLXSTT(booking_vocabulary=value)


def test_token_count_uses_installed_bpe_only_and_detects_drift(monkeypatch, sdk_source):
    from roma.providers.ai.whisper_vocabulary import PROMPT, load_prompt

    assert load_prompt(True) == PROMPT
    monkeypatch.setattr(sdk_source.tokenizer, "get_tokenizer", lambda *a, **kw: SimpleNamespace(
        encode=lambda text: list(range(33)),
    ))
    with pytest.raises(ValueError, match="token"):
        load_prompt(True)


def test_prompt_identity_drift_fails_before_optional_runtime(monkeypatch):
    from roma.providers.ai import whisper_vocabulary

    monkeypatch.setattr(whisper_vocabulary, "PROMPT", "private caller name")
    with pytest.raises(ValueError, match="identity"):
        whisper_vocabulary.load_prompt(True)


def test_prompt_budget_bounds_decoder_work_without_eot(sdk_source):
    from roma.providers.ai.mlx_batch import compact_greedy_loop
    from roma.providers.ai.whisper_vocabulary import PROMPT_SAMPLE_LEN

    calls = []

    class Inference:
        def logits(self, inputs, features):
            calls.append(inputs.shape[-1])
            logits = np.full((1, inputs.shape[-1], 3), -100.0)
            logits[:, -1, 1] = 0
            return logits

    # The declared upper bound includes more context than the actual 24-token list.
    task = SimpleNamespace(
        inference=Inference(), tokenizer=SimpleNamespace(eot=2, no_speech=0),
        decoder=sdk_source.decoding.GreedyDecoder(0, 2), logit_filters=[],
        sample_len=PROMPT_SAMPLE_LEN, n_ctx=448, sot_index=33,
    )
    prefix = np.zeros((1, 37), dtype=np.int32)
    tokens, _, _ = compact_greedy_loop(task, np.zeros((1, 1)), prefix)
    assert len(calls) == 410 and tokens.shape == (1, 447)
    assert calls[0] == 37 and all(size == 1 for size in calls[1:])


@pytest.mark.parametrize("enabled", [False, True])
def test_single_call_only_receives_fixed_opt_in_prompt(monkeypatch, tmp_path, enabled):
    from roma.providers.ai.whisper_vocabulary import PROMPT

    calls = []
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setattr(whisper_mlx, "verify_mlx_artifacts", lambda path: {})
    core = SimpleNamespace(set_cache_limit=lambda n: None, clear_cache=lambda: None)
    monkeypatch.setitem(sys.modules, "mlx", SimpleNamespace(core=core))
    monkeypatch.setitem(sys.modules, "mlx.core", core)
    monkeypatch.setitem(sys.modules, "mlx_whisper", SimpleNamespace(
        transcribe=lambda samples, **kw: calls.append((samples, kw)) or {
            "text": "unrelated caller words", "language": "gu",
        },
    ))
    monkeypatch.setitem(sys.modules, "mlx_whisper.tokenizer", SimpleNamespace(
        get_tokenizer=lambda *a, **kw: SimpleNamespace(encode=lambda text: list(range(24))),
    ))
    monkeypatch.setattr(whisper_mlx, "turn_chunks", lambda pcm, *a: (pcm,))
    provider = whisper_mlx.WhisperMLXSTT(model_id=str(tmp_path), booking_vocabulary=enabled)
    pcm = b"\1\0" * 16000
    result = asyncio.run(provider.transcribe(STTRequest(
        pcm, AudioFormat(16000), "gu", {"initial_prompt": "private caller name 5551234567"},
    )))
    assert result.text == "unrelated caller words"
    assert len(calls) == 1
    np.testing.assert_array_equal(calls[0][0], np.full(16000, 1 / 32768, dtype=np.float32))
    options = calls[0][1]
    assert options["sample_len"] == (410 if enabled else 440)
    assert options["condition_on_previous_text"] is False
    if enabled:
        assert options["initial_prompt"] == PROMPT
    else:
        assert "initial_prompt" not in options
    assert "private caller" not in str(options)


def test_vocabulary_setting_is_opt_in_and_registry_is_lazy():
    from roma.providers.ai.registry import build_stt_provider

    default = SpeechSettings(_env_file=None, stt_provider="whisper_mlx")
    assert default.whisper_booking_vocabulary is False
    provider = build_stt_provider(default)
    assert provider.booking_vocabulary is False and provider._inference is None
    enabled = SpeechSettings(_env_file=None, stt_provider="whisper_mlx",
                             whisper_booking_vocabulary=True)
    provider = build_stt_provider(enabled)
    assert provider.booking_vocabulary is True and provider._inference is None


@pytest.mark.parametrize("enabled", [False, True])
def test_structural_worker_passes_one_full_coverage_batch(monkeypatch, tmp_path, enabled):
    calls = []
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setattr(whisper_mlx, "verify_mlx_artifacts", lambda path: {})
    core = SimpleNamespace(set_cache_limit=lambda n: None, clear_cache=lambda: None)
    monkeypatch.setitem(sys.modules, "mlx", SimpleNamespace(core=core))
    monkeypatch.setitem(sys.modules, "mlx.core", core)
    monkeypatch.setitem(sys.modules, "mlx_whisper", SimpleNamespace(
        transcribe=lambda *a, **kw: pytest.fail("second decoder"),
    ))
    monkeypatch.setitem(sys.modules, "mlx_whisper.tokenizer", SimpleNamespace(
        get_tokenizer=lambda *a, **kw: SimpleNamespace(encode=lambda text: list(range(24))),
    ))
    monkeypatch.setattr(whisper_mlx, "turn_chunks", lambda pcm, *a: (pcm[:16000], pcm[16000:]))

    def decode(folder, parts, language, policy, **options):
        calls.append((parts, language, options))
        return ""

    monkeypatch.setattr(whisper_mlx, "decode_batch", decode)
    pcm = b"\1\0" * 16000
    provider = whisper_mlx.WhisperMLXSTT(model_id=str(tmp_path), booking_vocabulary=enabled)
    result = asyncio.run(provider.transcribe(STTRequest(pcm, AudioFormat(16000), "gu")))
    assert len(calls) == 1 and b"".join(calls[0][0]) == pcm
    assert calls[0][1] == "gu"
    assert calls[0][2] == ({"booking_vocabulary": True} if enabled else {})
    assert result.text == "" and result.error_code == "ASR-BATCH-REJECTED"


@pytest.mark.parametrize("reject_first", [False, True])
@pytest.mark.parametrize("short_first", [False, True])
def test_prefixed_sdk_batch_positions_output_and_row_compaction(
    monkeypatch, reject_first, short_first, sdk_source,
):
    """Actual pinned SDK token/finalization logic with fake features and logits."""
    from roma.providers.ai.mlx_batch import decode_batch
    from roma.providers.ai.whisper_vocabulary import PROMPT

    mx, audio, decoding = sdk_source.core, sdk_source.audio, sdk_source.decoding
    get_tokenizer = sdk_source.tokenizer.get_tokenizer
    tokenizer = get_tokenizer(True, num_languages=100, language="en", task="transcribe")
    outputs = [tokenizer.encode(" first"), tokenizer.encode(" second row")]
    if not short_first:
        outputs.reverse()
    calls, captured = [], []
    real_task = decoding.DecodingTask
    sot_index = 1 + len(tokenizer.encode(" " + PROMPT))

    class FakeInference:
        kv_cache = None

        def __init__(self, model):
            pass

        def reset(self):
            self.kv_cache = None

        def logits(self, inputs, features):
            ids = [int(x) for x in features[:, 0, 0].tolist()]
            steps = [0] * len(ids) if self.kv_cache is None else [
                int(row[1]) + 1 for row in self.kv_cache[0].tolist()
            ]
            self.kv_cache = (mx.array(list(zip(ids, steps, strict=True))),)
            calls.append(ids)
            logits = mx.full((len(ids), inputs.shape[1], 51866), -100.0)
            for i, (identity, step) in enumerate(zip(ids, steps, strict=True)):
                if step == 0:
                    assert inputs[i, sot_index].item() == tokenizer.sot
                    expected_language = "gu" if identity == 0 else "en"
                    assert inputs[i, sot_index + 1].item() == tokenizer.encoding.encode_single_token(
                        "<|" + expected_language + "|>")
                    # A high value at the old index must not reject a good row.
                    logits[i, 0, tokenizer.no_speech] = 100
                    logits[i, sot_index, 0] = 0
                    logits[i, sot_index, tokenizer.no_speech] = (
                        100 if reject_first and identity == 0 else -8
                    )
                next_token = outputs[identity][step] if step < len(outputs[identity]) else tokenizer.eot
                logits[i, -1, next_token] = 0
            return logits

    class CapturingTask(real_task):
        def __init__(self, model, options):
            super().__init__(model, options)
            assert options.prompt == PROMPT and options.sample_len == 410
            assert self.sot_index == sot_index and self.sample_begin == sot_index + 4

        def run(self, batch):
            rows = super().run(batch)
            captured.extend(rows)
            return rows

    model = SimpleNamespace(
        dims=SimpleNamespace(n_text_ctx=448, n_vocab=51866, n_audio_ctx=1, n_audio_state=1),
        is_multilingual=True, num_languages=100,
        detect_language=lambda features, tok: (None, [
            {"gu": 0.99, "en": 0.01}, {"gu": 0.01, "en": 0.99},
        ]),
    )
    monkeypatch.setattr(decoding, "DecodingTask", CapturingTask)
    monkeypatch.setattr(decoding, "Inference", FakeInference)
    monkeypatch.setattr(sdk_source.holder, "get_model", lambda *a: model)
    monkeypatch.setattr(audio, "log_mel_spectrogram", lambda samples, **kw: mx.array(
        [[float(samples[0] > 0)]], dtype=mx.float32,
    ))
    monkeypatch.setattr(audio, "pad_or_trim", lambda mel, *a, **kw: mel)
    text = decode_batch("fake-only", [b"\0\0" * 16000, b"\1\0" * 16000], "gu",
                        booking_vocabulary=True)
    expected = [tokenizer.decode(row).strip() for row in outputs]
    assert text == " ".join(expected[1:] if reject_first else expected)
    assert [row.text for row in captured] == expected
    assert [len(ids) for ids in calls] == [2, 2, 1]
    assert calls[-1] == ([1] if short_first else [0])
    assert PROMPT not in text
