from __future__ import annotations

import base64
import importlib.util
import io
import json
import struct
import sys
import threading
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

_HANDLER = Path(__file__).resolve().parents[3] / "benchmarks/asr/hf_endpoint/handler.py"


def module():
    spec = importlib.util.spec_from_file_location("roma_hf_endpoint_template", _HANDLER)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def wav_payload(pcm=b"\x00\x00" * 160, *, channels=1, rate=16000, width=2):
    stream = io.BytesIO()
    with wave.open(stream, "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(width)
        output.setframerate(rate)
        output.writeframes(pcm)
    return {
        "inputs": {
            "audio": base64.b64encode(stream.getvalue()).decode(),
            "language": "hi",
            "decoder": "ctc",
        }
    }


def handler(monkeypatch, tmp_path, infer=lambda *args: "ok"):
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    return module().EndpointHandler(str(tmp_path), loader=lambda path: infer)


def test_import_is_standalone_and_does_not_import_ml(monkeypatch):
    import builtins

    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name.split(".")[0] in {"torch", "transformers", "roma", "huggingface_hub"}:
            pytest.fail("model import during module import")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    assert module().EndpointHandler


def test_loads_once_and_preserves_terms_digits_and_language(monkeypatch, tmp_path):
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    loads, calls = [], []

    def loader(path):
        loads.append(path)

        def infer(pcm, language, decoder):
            calls.append((pcm, language, decoder))
            return " SEO \n Google Ads Digital Marketing ૨૦૨૬ हिन्दी ગુજરાતી "

        return infer

    endpoint = module().EndpointHandler(str(tmp_path), loader=loader)
    data = wav_payload()
    data["inputs"].update(language="gu", decoder="rnnt")
    expected = {"text": "SEO Google Ads Digital Marketing 2026 हिन्दी ગુજરાતી", "language": "gu"}
    assert endpoint(data) == expected
    assert endpoint(data) == expected
    assert loads == [str(tmp_path)]
    assert calls[0] == (b"\x00\x00" * 160, "gu", "rnnt")


@pytest.mark.parametrize(
    "data",
    [
        None,
        [],
        {},
        {"inputs": {}},
        {"inputs": {"audio": "x", "language": "en", "decoder": "ctc"}},
        {"inputs": {"audio": "x", "language": "hi", "decoder": "other"}},
        {"inputs": {"audio": "%invalid", "language": "hi", "decoder": "ctc"}},
        {"inputs": {"audio": 42, "language": "hi", "decoder": "ctc"}},
    ],
)
def test_rejects_invalid_json_contract_before_inference(monkeypatch, tmp_path, data):
    endpoint = handler(
        monkeypatch, tmp_path, infer=lambda *args: pytest.fail("bad request reached model")
    )
    with pytest.raises(ValueError):
        endpoint(data)


@pytest.mark.parametrize(
    "data",
    [
        wav_payload(b""),
        wav_payload(rate=8000),
        wav_payload(channels=2),
        wav_payload(width=1),
        wav_payload(b"\x00\x00" * 480001),
    ],
)
def test_rejects_invalid_wav_format_duration_and_empty_data(monkeypatch, tmp_path, data):
    endpoint = handler(
        monkeypatch, tmp_path, infer=lambda *args: pytest.fail("invalid audio reached model")
    )
    with pytest.raises(ValueError):
        endpoint(data)


def test_accepts_exact_thirty_second_pcm_limit(monkeypatch, tmp_path):
    endpoint = handler(monkeypatch, tmp_path, infer=lambda pcm, *args: str(len(pcm)))
    assert endpoint(wav_payload(b"\x00" * 960000))["text"] == "960000"


def test_rejects_oversized_base64_and_truncated_wav(monkeypatch, tmp_path):
    endpoint = handler(monkeypatch, tmp_path)
    data = wav_payload()
    raw = base64.b64decode(data["inputs"]["audio"])
    data["inputs"]["audio"] = base64.b64encode(raw[:-2]).decode()
    with pytest.raises(ValueError, match="truncated"):
        endpoint(data)
    data["inputs"]["audio"] = "A" * 1400000
    with pytest.raises(ValueError, match="bounded"):
        endpoint(data)


def test_rejects_declared_oversized_wav_before_reading_frames(monkeypatch, tmp_path):
    endpoint = handler(monkeypatch, tmp_path)
    data = wav_payload()
    raw = bytearray(base64.b64decode(data["inputs"]["audio"]))
    raw[40:44] = struct.pack("<I", 960002)
    data["inputs"]["audio"] = base64.b64encode(raw).decode()
    with pytest.raises(ValueError, match="30 seconds"):
        endpoint(data)


def test_startup_requires_offline_and_mounted_directory(monkeypatch, tmp_path):
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    with pytest.raises(RuntimeError, match="HF_HUB_OFFLINE"):
        module().EndpointHandler(str(tmp_path))
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    with pytest.raises(RuntimeError, match="Mount"):
        module().EndpointHandler(str(tmp_path / "missing"))


@pytest.mark.parametrize("value", [None, "x" * 30001, "\ud800"])
def test_invalid_model_output_is_sanitized(monkeypatch, tmp_path, value):
    endpoint = handler(monkeypatch, tmp_path, infer=lambda *args: value)
    with pytest.raises(RuntimeError, match="invalid transcript"):
        endpoint(wav_payload())


def test_model_and_startup_exceptions_do_not_expose_secrets(monkeypatch, tmp_path):
    def fail(*args):
        raise RuntimeError("hf_private raw audio transcript")

    endpoint = handler(monkeypatch, tmp_path, infer=fail)
    with pytest.raises(RuntimeError) as exc:
        endpoint(wav_payload())
    assert "hf_private" not in str(exc.value)
    with pytest.raises(RuntimeError) as exc:
        module().EndpointHandler(str(tmp_path), loader=fail)
    assert "hf_private" not in str(exc.value)


def test_default_loader_uses_official_mounted_model_api(monkeypatch, tmp_path):
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    calls = []

    class Tensor:
        def __init__(self, samples):
            self.values = list(samples)

        def unsqueeze(self, axis):
            assert axis == 0
            return self

        def __truediv__(self, value):
            self.values = [v / value for v in self.values]
            return self

    class Context:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    def model(waveform, language, decoder):
        calls.append((waveform.values, language, decoder))
        return "૨૦૨૬"

    def load(path, **kwargs):
        calls.append((path, kwargs))
        return model

    monkeypatch.setitem(
        sys.modules,
        "huggingface_hub",
        SimpleNamespace(constants=SimpleNamespace(HF_HUB_OFFLINE=True)),
    )
    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(
            tensor=lambda samples, **kwargs: Tensor(samples),
            float32="float32",
            inference_mode=Context,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "transformers",
        SimpleNamespace(AutoModel=SimpleNamespace(from_pretrained=load)),
    )
    endpoint = module().EndpointHandler(str(tmp_path))
    assert endpoint(wav_payload(b"\x00\x80\xff\x7f")) == {"text": "2026", "language": "hi"}
    assert calls[0] == (str(tmp_path), {"trust_remote_code": True, "local_files_only": True})
    assert calls[1] == ([-1.0, 32767 / 32768], "hi", "ctc")
    assert len(json.dumps(endpoint(wav_payload())).encode()) < 131072


def test_rejects_half_sample_wav(monkeypatch, tmp_path):
    endpoint = handler(monkeypatch, tmp_path)
    with pytest.raises(ValueError, match="truncated"):
        endpoint(wav_payload(b"\x00\x00\x00"))


def test_rejects_output_that_exceeds_json_limit_after_escaping(monkeypatch, tmp_path):
    endpoint = handler(monkeypatch, tmp_path, infer=lambda *args: "\x00" * 30000)
    with pytest.raises(RuntimeError, match="invalid transcript"):
        endpoint(wav_payload())


def test_handler_rejects_concurrent_native_inference(monkeypatch, tmp_path):
    started, release = threading.Event(), threading.Event()

    def infer(*args):
        started.set()
        release.wait(1)
        return "ok"

    endpoint = handler(monkeypatch, tmp_path, infer=infer)
    result = []
    worker = threading.Thread(target=lambda: result.append(endpoint(wav_payload())))
    worker.start()
    try:
        assert started.wait(0.5)
        with pytest.raises(RuntimeError, match="busy"):
            endpoint(wav_payload())
    finally:
        release.set()
        worker.join(1)
    assert result == [{"text": "ok", "language": "hi"}]


def test_default_loader_rejects_stale_offline_sdk_state(monkeypatch, tmp_path):
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setitem(
        sys.modules,
        "huggingface_hub",
        SimpleNamespace(constants=SimpleNamespace(HF_HUB_OFFLINE=False)),
    )
    with pytest.raises(RuntimeError, match="initialization failed"):
        module().EndpointHandler(str(tmp_path))


def test_hosted_client_matches_standalone_handler_contract(monkeypatch, tmp_path):
    import asyncio

    import httpx
    from roma.providers.ai.contracts import AudioFormat, STTRequest
    from roma.providers.ai.stt import HuggingFaceSTT

    endpoint = handler(
        monkeypatch, tmp_path, infer=lambda pcm, language, decoder: "SEO Google Ads ૨૦૨૬"
    )

    def respond(request):
        assert request.headers["Authorization"] == "Bearer test-only-token"
        result = endpoint(json.loads(request.content))
        return httpx.Response(200, json=result)

    async def run():
        provider = HuggingFaceSTT(
            endpoint_url="https://test.invalid/asr",
            token="test-only-token",
            language="gu",
            transport=httpx.MockTransport(respond),
        )
        result = await provider.transcribe(STTRequest(b"\0\0" * 1600, AudioFormat(16000), "gu"))
        assert result.text == "SEO Google Ads 2026" and result.language == "gu"

    asyncio.run(run())
