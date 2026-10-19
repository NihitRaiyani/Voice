import asyncio
import hashlib
import io
import json
import sys
from types import SimpleNamespace

import pytest
from roma.providers.ai.contracts import (
    AudioFormat,
    ProviderUnavailable,
    TTSProvider,
    TTSRequest,
)
from roma.providers.ai.indic_tts import FILES, IndicTTSProvider, validate_request


def manifest(tmp_path):
    files = {}
    for name in FILES:
        path = tmp_path / name
        path.write_bytes(name.encode())
        files[name] = {"path": name, "sha256": hashlib.sha256(name.encode()).hexdigest()}
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps(
            {
                "schema": "roma-indic-tts-v1",
                "runtime_distribution": "TTS",
                "runtime_version": "0.22.0",
                "languages": {"hi": {"voices": ["female", "male"], "files": files}},
            }
        )
    )
    return path


def test_lazy_provider_needs_no_optional_runtime_and_missing_artifacts_actionable():
    async def run():
        provider = IndicTTSProvider()
        assert isinstance(provider, TTSProvider)
        with pytest.raises(ProviderUnavailable, match="INDIC_TTS_MANIFEST"):
            await provider.synthesize(TTSRequest("नमस्ते"))

    asyncio.run(run())


@pytest.mark.parametrize(
    "tts_request",
    [
        TTSRequest(""),
        TTSRequest("x", language="fr"),
        TTSRequest("x", voice="unknown"),
        TTSRequest("x", format=AudioFormat(22050)),
    ],
)
def test_invalid_requests_fail_before_loading(tts_request):
    async def run():
        with pytest.raises(ValueError):
            await IndicTTSProvider().synthesize(tts_request)

    asyncio.run(run())


def test_manifest_hash_and_runtime_identity_change_and_tampered_artifacts_fail(tmp_path):
    async def run():
        path = manifest(tmp_path)
        provider = IndicTTSProvider(manifest_path=str(path), python_path=sys.executable)
        request = TTSRequest("नमस्ते")
        first = await provider.cache_identity(request)
        assert first.startswith("indic-tts:")
        data = json.loads(path.read_text())
        data["runtime_version"] = "0.13.3"
        path.write_text(json.dumps(data))
        assert await provider.cache_identity(request) != first
        (tmp_path / "tts_checkpoint").write_bytes(b"tampered")
        with pytest.raises(ProviderUnavailable, match="SHA256"):
            await provider.cache_identity(request)

    asyncio.run(run())


def test_missing_speakers_and_path_escape_fail(tmp_path):
    async def run():
        path = manifest(tmp_path)
        data = json.loads(path.read_text())
        del data["languages"]["hi"]["files"]["tts_speakers_file"]
        path.write_text(json.dumps(data))
        with pytest.raises(ProviderUnavailable):
            await IndicTTSProvider(
                manifest_path=str(path), python_path=sys.executable
            ).cache_identity(TTSRequest("hi"))
        path = manifest(tmp_path)
        data = json.loads(path.read_text())
        data["languages"]["hi"]["files"]["tts_checkpoint"]["path"] = "../outside.pth"
        path.write_text(json.dumps(data))
        with pytest.raises(ProviderUnavailable):
            await IndicTTSProvider(
                manifest_path=str(path), python_path=sys.executable
            ).cache_identity(TTSRequest("hi"))

    asyncio.run(run())


def test_adapter_subprocess_transport_returns_validated_pcm_without_optional_runtime(tmp_path):
    async def run():
        manifest_path = manifest(tmp_path)
        interpreter = tmp_path / "synthetic-interpreter"
        interpreter.write_text(
            "#!"
            + sys.executable
            + "\nimport json,sys,pathlib\np=json.loads(sys.stdin.read())\npathlib.Path(p['output']).write_bytes(bytes(320))\n"
        )
        interpreter.chmod(0o700)
        provider = IndicTTSProvider(
            manifest_path=str(manifest_path), python_path=str(interpreter)
        )
        result = await provider.synthesize(TTSRequest("नमस्ते"))
        assert result.audio == bytes(320) and result.duration_ms == 20

    asyncio.run(run())


def test_adapter_cancellation_kills_and_reaps_worker(tmp_path, monkeypatch):
    class Process:
        returncode = None

        def __init__(self):
            self.started = asyncio.Event()
            self.killed = False
            self.reaped = False

        async def communicate(self, data):
            self.started.set()
            await asyncio.Event().wait()

        def kill(self):
            self.killed = True
            self.returncode = -9

        async def wait(self):
            self.reaped = True

    async def run():
        process = Process()

        async def create(*args, **kwargs):
            return process

        monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
        provider = IndicTTSProvider(
            manifest_path=str(manifest(tmp_path)), python_path=sys.executable
        )
        task = asyncio.create_task(provider.synthesize(TTSRequest("नमस्ते")))
        await process.started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert process.killed and process.reaped

    asyncio.run(run())


@pytest.mark.parametrize(
    "fmt",
    [
        AudioFormat(8000.0),
        AudioFormat(True),
        AudioFormat(8000, channels=True),
        AudioFormat(8000, channels=1.0),
        AudioFormat(8000, encoding=[]),
        None,
    ],
)
def test_strict_format_types_rejected_before_loading(fmt):
    with pytest.raises(ValueError):
        validate_request(TTSRequest("hello", format=fmt))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"language": []},
        {"language": None},
        {"language": 1},
        {"voice": []},
        {"voice": {}},
        {"voice": 1},
        {"text": []},
        {"text": None},
        {"metadata": []},
        {"metadata": {1: "value"}},
        {"metadata": {"key": []}},
        {"metadata": {"key": "x" * 257}},
    ],
)
def test_malformed_request_values_fail_as_value_error(kwargs):
    with pytest.raises(ValueError):
        validate_request(TTSRequest(**{"text": "hello", **kwargs}))


def test_cancelled_singleflight_reaps_worker_after_last_waiter(tmp_path, monkeypatch):
    from roma.domain.safety.audit import SafetyContext
    from roma.services.safety_service import SafetyService
    from roma.services.tts_service import SpeechSynthesisService

    class Process:
        returncode = None

        def __init__(self):
            self.started = asyncio.Event()
            self.killed = self.reaped = False

        async def communicate(self, data):
            self.started.set()
            await asyncio.Event().wait()

        def kill(self):
            self.killed = True
            self.returncode = -9

        async def wait(self):
            self.reaped = True

    async def run():
        process = Process()

        async def create(*args, **kwargs):
            return process

        monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
        joined = asyncio.Event()

        class ObservedProvider(IndicTTSProvider):
            identities = 0

            async def cache_identity(self, request):
                version = await super().cache_identity(request)
                self.identities += 1
                if self.identities == 2:
                    joined.set()
                return version

        provider = ObservedProvider(
            manifest_path=str(manifest(tmp_path)), python_path=sys.executable
        )
        synth = SpeechSynthesisService(provider, SafetyService(), model_version="manifest")
        context = SafetyContext("synthetic-shared-tts", 1, "lab")
        first = asyncio.create_task(synth.synthesize(TTSRequest("hello"), context))
        await process.started.wait()
        second = asyncio.create_task(synth.synthesize(TTSRequest("hello"), context))
        async with asyncio.timeout(2):
            await joined.wait()
        assert synth.coalesced == 1
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert not process.killed
        second.cancel()
        with pytest.raises(asyncio.CancelledError):
            await second
        assert process.killed and process.reaped and not synth._cache and not synth._flights

    asyncio.run(run())


@pytest.mark.parametrize("nested", [False, True])
def test_worker_rebinds_release_speakers_without_changing_verified_artifacts(tmp_path, nested):
    from roma.providers.ai._indic_tts_worker import _runtime_paths

    config = {"model": "fast_pitch", "speakers_file": "models/v1/gu/fastpitch/speakers.pth"}
    if nested:
        config["model_args"] = {
            "speakers_file": "models/v1/gu/fastpitch/speakers.pth",
            "num_speakers": 2,
        }
    source = tmp_path / "config.json"
    source.write_text(json.dumps(config))
    original = source.read_bytes()
    speakers = tmp_path / "speakers.pth"
    speakers.write_bytes(b"verified-speakers")
    paths = {"tts_config_path": str(source), "tts_speakers_file": str(speakers)}
    with _runtime_paths(paths) as resolved:
        runtime_config = type(source)(resolved["tts_config_path"])
        assert runtime_config != source and runtime_config.is_file()
        expected = {**config, "speakers_file": str(speakers)}
        if nested:
            expected["model_args"] = {"speakers_file": str(speakers), "num_speakers": 2}
        assert json.loads(runtime_config.read_text()) == expected
        assert resolved["tts_speakers_file"] == str(speakers)
    assert not runtime_config.exists()
    assert source.read_bytes() == original
    assert paths["tts_config_path"] == str(source)


def test_worker_temporary_config_removed_if_model_loading_fails(tmp_path):
    from roma.providers.ai._indic_tts_worker import _runtime_paths

    source = tmp_path / "config.json"
    source.write_text('{"model": "fast_pitch"}')
    with pytest.raises(RuntimeError, match="checkpoint failed"):
        with _runtime_paths(
            {"tts_config_path": str(source), "tts_speakers_file": "speakers"}
        ) as paths:
            runtime_config = type(source)(paths["tts_config_path"])
            raise RuntimeError("checkpoint failed")
    assert not runtime_config.exists()
    assert json.loads(source.read_text()) == {"model": "fast_pitch"}


def test_worker_passes_rebound_config_to_runtime_without_optional_dependencies(
    tmp_path, monkeypatch
):
    from roma.providers.ai import _indic_tts_worker as worker

    source = tmp_path / "config.json"
    source.write_text(json.dumps({"speakers_file": "missing/legacy/speakers.pth"}))
    speakers = tmp_path / "speakers.pth"
    speakers.write_bytes(b"reviewed-speakers")
    observed = []

    def synthesizer(**kwargs):
        config_path = type(source)(kwargs["tts_config_path"])
        observed.append(config_path)
        assert json.loads(config_path.read_text())["speakers_file"] == str(speakers)
        assert kwargs["tts_speakers_file"] == str(speakers)
        assert kwargs["use_cuda"] is False
        raise RuntimeError("stopped before checkpoint loading")

    monkeypatch.setattr(worker.importlib.metadata, "version", lambda name: "0.22.0")
    monkeypatch.setattr(
        sys,
        "stdin",
        SimpleNamespace(
            buffer=io.BytesIO(
                json.dumps(
                    {
                        "runtime_distribution": "TTS",
                        "runtime_version": "0.22.0",
                        "paths": {
                            "tts_config_path": str(source),
                            "tts_speakers_file": str(speakers),
                        },
                    }
                ).encode()
            )
        ),
    )
    monkeypatch.setitem(sys.modules, "numpy", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "scipy.signal", SimpleNamespace(resample_poly=None))
    monkeypatch.setitem(
        sys.modules, "TTS.utils.synthesizer", SimpleNamespace(Synthesizer=synthesizer)
    )
    with pytest.raises(RuntimeError, match="stopped before checkpoint loading"):
        worker.main()
    assert observed and not observed[0].exists()
    assert json.loads(source.read_text()) == {"speakers_file": "missing/legacy/speakers.pth"}


@pytest.mark.parametrize("unsupported", [["x"], ["\u094d"], ["9"]])
def test_worker_rejects_unsupported_cleaned_letters_marks_and_numbers(unsupported):
    from roma.providers.ai._indic_tts_worker import _validate_vocabulary

    class Tokenizer:
        not_found_characters = ()

        def text_to_ids(self, text):
            assert text == "synthetic input"
            self.not_found_characters = unsupported
            return [1, 2]

    with pytest.raises(ValueError, match="vocabulary"):
        _validate_vocabulary(Tokenizer(), "synthetic input")


@pytest.mark.parametrize(
    "text,unsupported",
    [
        ("નમસ્તે, રોમા. એસ ઈ ઓ", []),
        ("नमस्ते, रोमा। एस ई ओ", []),
        ("Hello, Roma. S E O", ["—", "🙂"]),
    ],
)
def test_worker_uses_model_tokenizer_and_accepts_supported_speech(text, unsupported):
    from roma.providers.ai._indic_tts_worker import _validate_vocabulary

    class Tokenizer:
        not_found_characters = ()
        called = False

        def text_to_ids(self, actual):
            assert actual == text
            self.called = True
            self.not_found_characters = unsupported
            return [1, 2]

    tokenizer = Tokenizer()
    _validate_vocabulary(tokenizer, text)
    assert tokenizer.called


@pytest.mark.parametrize("audit_failure", [False, True])
def test_gujarati_safety_replacement_uses_supported_hindi_but_raw_latin_still_fails(
    audit_failure,
):
    import unicodedata

    from roma.domain.safety.audit import SafetyContext
    from roma.domain.safety.lexicon import HARD_FAIL_LINE, SUBSTITUTIONS, BlockCategory
    from roma.domain.safety.pronunciation import pronunciation_for
    from roma.providers.ai._indic_tts_worker import _validate_vocabulary
    from roma.providers.ai.contracts import TTSResult
    from roma.services.safety_service import InMemorySafetyEventStore, SafetyService
    from roma.services.tts_service import SpeechSynthesisService

    class NativeTokenizer:
        def text_to_ids(self, text):
            self.not_found_characters = [
                char for char in text if unicodedata.name(char, "").startswith("LATIN")
            ]
            return [1, 2]

    class VocabularyCheckedProvider:
        def __init__(self):
            self.seen = []

        async def synthesize(self, request):
            self.seen.append(request)
            _validate_vocabulary(NativeTokenizer(), request.text)
            assert request.language == "hi"
            return TTSResult(b"\x00\x00" * 800, AudioFormat(8000))

    class BrokenStore:
        async def record(self, context, events):
            raise OSError("Synthetic audit failure")

    async def run():
        store = BrokenStore() if audit_failure else InMemorySafetyEventStore()
        provider = VocabularyCheckedProvider()
        service = SpeechSynthesisService(
            provider, SafetyService(store), model_version="native-v1"
        )
        canonical = HARD_FAIL_LINE if audit_failure else SUBSTITUTIONS[BlockCategory.FEE]
        for turn in (1, 2):
            result = await service.synthesize(
                TTSRequest("ફી 50000", language="gu"),
                SafetyContext("synthetic-gu-vocabulary", turn, "lab"),
            )
            assert result.text == pronunciation_for(canonical).spoken_text
            assert result.audit_ok is (not audit_failure)
        assert len(provider.seen) == 1
        assert service.stores == service.hits == 1
        if not audit_failure:
            assert store.entries
        # Ordinary unsupported text still fails; no weakening of the worker guard.
        with pytest.raises(ValueError, match="vocabulary"):
            await service.synthesize(
                TTSRequest("ordinary Roman input", language="gu"),
                SafetyContext("synthetic-gu-vocabulary", 3, "lab"),
            )
        assert service.stores == 1 and len(service._cache) == 1

    asyncio.run(run())
