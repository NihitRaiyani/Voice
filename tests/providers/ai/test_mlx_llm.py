"""No weights, downloads or optional MLX imports in automated tests."""

import asyncio
import threading
import time
from types import SimpleNamespace

import pytest
from roma.core.local_llm_config import LocalLLMSettings
from roma.providers.ai.contracts import LLMMessage, LLMProvider, LLMRequest, ProviderUnavailable
from roma.providers.ai.mlx_llm import Qwen3MLXLLM
from roma.providers.ai.registry import build_llm_provider


def fake_provider(**settings):
    provider = Qwen3MLXLLM(LocalLLMSettings(_env_file=None, **settings))
    provider._model = SimpleNamespace(args=SimpleNamespace(max_position_embeddings=10000))
    provider._tokenizer = SimpleNamespace(
        apply_chat_template=lambda *a, **k: "synthetic prompt",
        encode=lambda text: [1, 2, 3],
    )
    provider._sampler = lambda **kw: kw
    return provider


def response(text="hello", count=1, finish=None):
    return SimpleNamespace(text=text, generation_tokens=count, finish_reason=finish)


def request(**kwargs):
    return LLMRequest((LLMMessage("user", "synthetic caller"),), **kwargs)


def collect(provider, req=None):
    async def run():
        return [chunk async for chunk in provider.generate(req or request())]

    return asyncio.run(run())


def test_registry_is_lazy_and_preserves_interface():
    provider = build_llm_provider(LocalLLMSettings(_env_file=None, llm_provider="qwen3_mlx"))
    assert isinstance(provider, Qwen3MLXLLM)
    assert isinstance(provider, LLMProvider)
    assert provider._model is None


def test_streaming_metrics_and_non_thinking_template():
    provider = fake_provider()
    seen = {}

    def template(messages, **kwargs):
        seen.update(kwargs)
        return "prompt"

    provider._tokenizer.apply_chat_template = template

    # Real SDK returns a closable generator.
    def stream(*args, **kwargs):
        yield response()
        yield response(" world", 2, "stop")

    provider._stream = stream
    chunks = collect(provider)
    assert "".join(c.text for c in chunks) == "hello world"
    assert seen["enable_thinking"] is False
    assert chunks[-1].finish_reason == "stop"
    metrics = chunks[-1].metrics
    assert metrics.generated_tokens == 2 and metrics.prompt_tokens == 3
    assert metrics.dtype == "int4" and metrics.ttft_ms is not None


@pytest.mark.parametrize(
    "kwargs",
    [{"max_tokens": 0}, {"max_tokens": 513}, {"temperature": float("nan")}, {"model": "other"}],
)
def test_invalid_requests_rejected_before_loading(kwargs):
    with pytest.raises(ValueError):
        collect(Qwen3MLXLLM(), request(**kwargs))


def test_prompt_budget_never_truncates_rules():
    provider = fake_provider(local_llm_max_prompt_tokens=64)
    provider._tokenizer.encode = lambda text: list(range(65))
    with pytest.raises(ValueError, match="refusing to truncate"):
        collect(provider)


@pytest.mark.parametrize("error_type", [RuntimeError, ValueError])
def test_sdk_error_is_sanitized(error_type):
    provider = fake_provider()

    def fail(*args, **kwargs):
        raise error_type("synthetic-secret")

    provider._stream = fail
    with pytest.raises(ProviderUnavailable, match="generation failed") as exc:
        collect(provider)
    assert "synthetic-secret" not in str(exc.value)


def test_timeout_closes_worker_stream():
    provider = fake_provider(local_llm_generation_timeout_secs=0.001)
    closed = threading.Event()

    def stream(*args, **kwargs):
        try:
            time.sleep(0.01)
            yield response()
        finally:
            closed.set()

    provider._stream = stream
    with pytest.raises(ProviderUnavailable, match="time budget"):
        collect(provider)
    assert closed.is_set()


def test_closing_consumer_stops_worker_before_next_request():
    provider = fake_provider()
    closed = threading.Event()

    def stream(*args, **kwargs):
        try:
            for n in range(1000):
                time.sleep(0.001)
                yield response(count=n + 1)
        finally:
            closed.set()

    provider._stream = stream

    async def run():
        output = provider.generate(request())
        await anext(output)
        await output.aclose()
        assert closed.is_set()
        assert not provider._lock.locked()

    asyncio.run(run())


def test_prefill_respects_time_budget_before_first_token():
    provider = fake_provider(local_llm_generation_timeout_secs=0.001)
    closed = threading.Event()

    def stream(*args, **kwargs):
        try:
            time.sleep(0.01)
            kwargs["prompt_progress_callback"](256, 1000)
            yield response()
        finally:
            closed.set()

    provider._stream = stream
    with pytest.raises(ProviderUnavailable, match="time budget"):
        collect(provider)
    assert closed.is_set()


def test_loading_pins_cache_revision_and_secret_boundary(monkeypatch, tmp_path):
    import json
    import sys
    from types import ModuleType

    cache = tmp_path / ("a" * 40)
    cache.mkdir()
    (cache / "config.json").write_text(json.dumps({"quantization": {"bits": 4}}))
    seen = {}
    sdk = ModuleType("mlx_lm")
    model = SimpleNamespace(parameters=lambda: [], args=SimpleNamespace())

    def load(path, **kwargs):
        seen["load"] = kwargs
        return model, object()

    sdk.load = load
    sdk.stream_generate = lambda *a, **k: None
    sample = ModuleType("mlx_lm.sample_utils")
    sample.make_sampler = lambda **kw: kw
    hub = ModuleType("huggingface_hub")

    def snapshot(*args, **kwargs):
        seen["snapshot"] = kwargs
        return str(cache)

    hub.snapshot_download = snapshot
    mlx = ModuleType("mlx")
    core = ModuleType("mlx.core")
    core.eval = lambda *args: None
    mlx.core = core
    for name, module in {
        "mlx": mlx,
        "mlx.core": core,
        "mlx_lm": sdk,
        "mlx_lm.sample_utils": sample,
        "huggingface_hub": hub,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr("roma.providers.ai.mlx_llm.platform.system", lambda: "Darwin")
    monkeypatch.setattr("roma.providers.ai.mlx_llm.platform.machine", lambda: "arm64")
    provider = Qwen3MLXLLM(
        LocalLLMSettings(
            _env_file=None,
            local_llm_revision="a" * 40,
            local_llm_dtype="int4",
            hf_token="synthetic-secret",
        )
    )
    provider._load()
    assert seen["snapshot"]["local_files_only"] is True
    assert seen["snapshot"]["revision"] == "a" * 40
    assert seen["snapshot"]["token"] == "synthetic-secret"
    assert seen["load"]["tokenizer_config"]["trust_remote_code"] is False
    assert provider._revision == "a" * 40
    assert "synthetic-secret" not in repr(provider.settings)
    # Check a mislabeled profile before the SDK can materialize its weights.
    (cache / "config.json").write_text(json.dumps({"quantization": {"bits": 8}}))
    seen.pop("load")
    rejected = Qwen3MLXLLM(provider.settings)
    with pytest.raises(ProviderUnavailable, match="not 4-bit"):
        rejected._load()
    assert "load" not in seen


def test_non_apple_host_rejected_before_optional_import(monkeypatch):
    monkeypatch.setattr("roma.providers.ai.mlx_llm.platform.system", lambda: "Linux")
    with pytest.raises(ProviderUnavailable, match="Apple-silicon"):
        Qwen3MLXLLM()._load()


def test_repeated_requests_reuse_one_sdk_worker():
    provider = fake_provider()
    threads = []

    def stream(*args, **kwargs):
        threads.append(threading.current_thread())
        yield response(finish="stop")

    provider._stream = stream

    async def run():
        for _ in range(3):
            assert [c async for c in provider.generate(request())]

    asyncio.run(run())
    assert len(threads) == 3
    assert threads[0] is threads[1] is threads[2]
