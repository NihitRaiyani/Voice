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
        seen["sampler"] = kwargs["sampler"]
        yield response()
        yield response(" world", 2, "stop")

    provider._stream = stream
    chunks = collect(provider)
    assert "".join(c.text for c in chunks) == "hello world"
    assert seen["enable_thinking"] is False
    assert seen["sampler"]["top_p"] == 0.8
    assert seen["sampler"]["top_k"] == 20
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
    tokenizer = SimpleNamespace(chat_template="synthetic template")

    def load(path, **kwargs):
        seen["load"] = kwargs
        return model, tokenizer

    sdk.load = load
    sdk.stream_generate = lambda *a, **k: None
    sample = ModuleType("mlx_lm.sample_utils")
    sample.make_sampler = lambda **kw: kw
    cache_sdk = ModuleType("mlx_lm.models.cache")
    cache_sdk.make_prompt_cache = lambda model: []
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
        "mlx_lm.models.cache": cache_sdk,
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
    assert "*.jinja" in seen["snapshot"]["allow_patterns"]
    assert seen["snapshot"]["token"] == "synthetic-secret"
    assert seen["load"]["tokenizer_config"]["trust_remote_code"] is False
    assert provider._revision == "a" * 40
    assert "synthetic-secret" not in repr(provider.settings)
    tokenizer.chat_template = None
    with pytest.raises(ProviderUnavailable, match="chat template is missing"):
        Qwen3MLXLLM(provider.settings)._load()
    tokenizer.chat_template = "synthetic template"
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


def cached_fake():
    provider = fake_provider()

    class Model:
        args = SimpleNamespace(max_position_embeddings=10000)

        def __init__(self):
            self.prefills = []

        def __call__(self, ids, cache):
            self.prefills.append(ids[0])
            cache[0].state.extend(ids[0])

    provider._model = Model()
    provider._mx = SimpleNamespace(array=lambda ids: ids, eval=lambda ids: None)
    provider._make_cache = lambda model: [SimpleNamespace(state=[])]
    provider._tokenizer.apply_chat_template = lambda messages, **kw: "".join(
        m["content"] + "|" for m in messages
    )
    provider._tokenizer.encode = lambda text: list(text.encode())
    provider._stream = None
    return provider


def cached_request(policy="policy", caller="caller", **metadata):
    return LLMRequest(
        (LLMMessage("system", policy), LLMMessage("user", caller)),
        metadata={"cache_prefix": "system-v1", **metadata},
    )


def test_cached_prefix_excludes_callers_and_generation_mutation():
    provider = cached_fake()
    seen = []

    def stream(model, tokenizer, ids, **kwargs):
        cache = kwargs["prompt_cache"]
        assert cache[0].state == list(b"policy|")
        seen.append(ids)
        cache[0].state.extend([*ids, 99])
        yield response(finish="stop")

    provider._stream = stream
    first = collect(provider, cached_request(caller="caller A"))[-1].metrics
    second = collect(provider, cached_request(caller="caller B"))[-1].metrics
    assert not first.prefix_cache_hit and first.cached_prompt_tokens == 0
    assert second.prefix_cache_hit and second.cached_prompt_tokens == len(b"policy|")
    assert provider._model.prefills == [list(b"policy|")]
    assert seen == [list(b"caller A|"), list(b"caller B|")]
    assert next(iter(provider._prefixes.values()))[0].state == list(b"policy|")


def test_prefix_cache_is_bounded_and_invalidates_policy_and_model_identity():
    provider = cached_fake()

    def stream(*args, **kwargs):
        yield response(finish="stop")

    provider._stream = stream
    for number in range(5):
        assert not collect(provider, cached_request(policy=f"policy {number}"))[
            -1
        ].metrics.prefix_cache_hit
    assert len(provider._prefixes) == 4
    assert collect(provider, cached_request(policy="policy 4"))[-1].metrics.prefix_cache_hit
    provider._revision = "changed revision"
    assert not collect(provider, cached_request(policy="policy 4"))[-1].metrics.prefix_cache_hit


@pytest.mark.parametrize("disabled", [True, False])
def test_cache_requires_opt_in_and_configuration(disabled):
    provider = cached_fake()
    provider.settings.local_llm_prefix_cache = not disabled

    def stream(*args, **kwargs):
        assert "prompt_cache" not in kwargs
        yield response(finish="stop")

    provider._stream = stream
    req = cached_request() if disabled else request()
    assert not collect(provider, req)[-1].metrics.prefix_cache_hit
    assert not provider._prefixes


def test_failed_generation_cannot_poison_cached_prefix():
    provider = cached_fake()

    def stream(*args, **kwargs):
        kwargs["prompt_cache"][0].state.append(999)
        raise RuntimeError("synthetic failure")
        yield

    provider._stream = stream
    with pytest.raises(ProviderUnavailable):
        collect(provider, cached_request())
    assert next(iter(provider._prefixes.values()))[0].state == list(b"policy|")


def test_token_boundary_mismatch_does_not_reuse_cache():
    provider = cached_fake()
    provider._tokenizer.encode = lambda text: [len(text)]

    def stream(*args, **kwargs):
        assert "prompt_cache" not in kwargs
        yield response(finish="stop")

    provider._stream = stream
    assert collect(provider, cached_request())
    assert not provider._prefixes


def test_timed_out_prefix_prefill_is_not_published():
    provider = cached_fake()
    provider.settings.local_llm_generation_timeout_secs = 0.001
    original = provider._model

    class SlowModel:
        args = original.args

        def __call__(self, ids, cache):
            time.sleep(0.01)
            original(ids, cache)

    provider._model = SlowModel()
    with pytest.raises(ProviderUnavailable, match="time budget"):
        collect(provider, cached_request())
    assert not provider._prefixes


def test_cancelled_suffix_never_enters_next_call_cache():
    provider = cached_fake()
    closed = threading.Event()

    def stream(*args, **kwargs):
        assert kwargs["prompt_cache"][0].state == list(b"policy|")
        kwargs["prompt_cache"][0].state.append(999)
        try:
            for n in range(100):
                time.sleep(0.001)
                yield response(count=n + 1)
        finally:
            closed.set()

    provider._stream = stream

    async def run():
        first = provider.generate(cached_request(caller="caller A"))
        await anext(first)
        await first.aclose()
        assert closed.is_set()
        second = provider.generate(cached_request(caller="caller B"))
        await anext(second)
        await second.aclose()

    asyncio.run(run())
    assert next(iter(provider._prefixes.values()))[0].state == list(b"policy|")
