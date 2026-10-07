"""Offline adapter tests. No SDK imports, weights, tokens or network required."""

import asyncio
import queue
import time
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from roma.core.local_llm_config import LocalLLMSettings
from roma.providers.ai.contracts import LLMMessage, LLMRequest, ProviderUnavailable
from roma.providers.ai.transformers_llm import Qwen3TransformersLLM, select_runtime


def settings(**values):
    return LocalLLMSettings(_env_file=None, **values)


def runtime(cuda=False, mps=False, bf16=False):
    return SimpleNamespace(
        cuda=SimpleNamespace(is_available=lambda: cuda, is_bf16_supported=lambda: bf16),
        backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: mps)),
    )


@pytest.mark.parametrize(
    ("cuda", "mps", "bf16", "expected"),
    [
        (True, False, True, ("cuda", "bf16")),
        (True, False, False, ("cuda", "fp16")),
        (False, True, False, ("mps", "fp16")),
        (False, False, False, ("cpu", "fp32")),
    ],
)
def test_automatic_device_and_dtype(cuda, mps, bf16, expected):
    assert select_runtime(runtime(cuda, mps, bf16), "auto", "auto") == expected


@pytest.mark.parametrize(
    ("device", "dtype"), [("cuda", "fp16"), ("mps", "fp16"), ("cpu", "fp16"), ("cpu", "int4")]
)
def test_unsupported_runtime_rejected(device, dtype):
    with pytest.raises(ProviderUnavailable):
        select_runtime(runtime(), device, dtype)


def test_local_settings_need_no_cloud_keys_and_hide_hf_token():
    config = settings(HF_TOKEN="synthetic-secret")
    assert config.local_llm_local_files_only
    assert "synthetic-secret" not in repr(config)
    assert config.hf_token.get_secret_value() == "synthetic-secret"
    with pytest.raises(ValidationError):
        settings(local_llm_max_new_tokens=0)


class Streamer:
    def __init__(self, tokenizer, **kwargs):
        self.next_tokens_are_prompt = True
        self.queue = queue.Queue()
        self.timeout = kwargs["timeout"]

    def put(self, value):
        if self.next_tokens_are_prompt:
            self.next_tokens_are_prompt = False
        else:
            self.queue.put(value)

    def end(self):
        self.queue.put(None)

    def __next__(self):
        value = self.queue.get(timeout=self.timeout)
        if value is None:
            raise StopIteration
        return value


class Inputs(dict):
    def to(self, device):
        self.device = device
        return self


class Tokenizer:
    pad_token_id = 0
    eos_token_id = 1

    def apply_chat_template(self, messages, **kwargs):
        self.template_options = kwargs
        self.messages = messages
        return "prompt"

    def __call__(self, prompt, **kwargs):
        self.tokenization_options = kwargs
        return Inputs(input_ids=SimpleNamespace(shape=(1, 3)))


def fake_provider(*, fail=False, slow=False):
    provider = Qwen3TransformersLLM(settings())
    tokenizer = Tokenizer()
    stopped = []

    def generate(**kwargs):
        provider.options = kwargs
        if fail:
            raise RuntimeError("synthetic-secret must never leak")
        stream = kwargs["streamer"]
        stream.put("prompt")
        for text in ["Namaste ", "ji"]:
            stream.put(text)
            if slow:
                time.sleep(0.05)
            if kwargs["stopping_criteria"][0](None, None):
                stopped.append(True)
                break
        stream.end()
        return SimpleNamespace(shape=(1, 5))

    provider._model = SimpleNamespace(
        device="cpu", config=SimpleNamespace(max_position_embeddings=1024), generate=generate
    )
    provider._tokenizer = tokenizer
    provider._torch = SimpleNamespace(inference_mode=nullcontext)
    provider._transformers = SimpleNamespace(
        TextIteratorStreamer=Streamer, StoppingCriteria=object, StoppingCriteriaList=list
    )
    provider._device, provider._dtype = "cpu", "fp32"
    return provider, stopped


def test_stream_uses_template_budgets_and_token_metrics():
    async def run():
        provider, _ = fake_provider()
        chunks = [
            c
            async for c in provider.generate(
                LLMRequest((LLMMessage("user", "hello"),), max_tokens=5, temperature=0)
            )
        ]
        assert "".join(c.text for c in chunks) == "Namaste ji"
        assert chunks[-1].metrics.generated_tokens == 2
        assert chunks[-1].metrics.prompt_tokens == 3
        assert chunks[-1].metrics.ttft_ms >= 0
        assert chunks[-1].metrics.tokens_per_second > 0
        assert provider._tokenizer.template_options["enable_thinking"] is False
        assert provider._tokenizer.tokenization_options["return_token_type_ids"] is False
        assert provider.options["max_new_tokens"] == 5
        assert provider.options["do_sample"] is False
        assert "temperature" not in provider.options

    asyncio.run(run())


def test_generation_failure_is_redacted_and_releases_lock():
    async def run():
        provider, _ = fake_provider(fail=True)
        with pytest.raises(ProviderUnavailable, match="Local generation failed") as error:
            _ = [c async for c in provider.generate(LLMRequest((LLMMessage("user", "hi"),)))]
        assert "synthetic-secret" not in str(error.value)
        assert not provider._lock.locked()

    asyncio.run(run())


def test_cancellation_waits_for_worker_stop_before_reusing_model():
    async def run():
        provider, stopped = fake_provider(slow=True)
        iterator = provider.generate(LLMRequest((LLMMessage("user", "hi"),)))
        await anext(iterator)
        await iterator.aclose()
        assert stopped
        assert not provider._lock.locked()

    asyncio.run(run())


def test_timeout_stops_worker_and_does_not_report_success():
    async def run():
        provider, stopped = fake_provider(slow=True)
        provider.settings = settings(local_llm_generation_timeout_secs=0.01)
        with pytest.raises(ProviderUnavailable, match="timed out"):
            _ = [c async for c in provider.generate(LLMRequest((LLMMessage("user", "hi"),)))]
        assert stopped
        assert not provider._lock.locked()

    asyncio.run(run())


def test_context_overflow_rejected_without_truncating_rules():
    async def run():
        provider, _ = fake_provider()
        provider._model.config.max_position_embeddings = 100
        with pytest.raises(ValueError, match="context"):
            _ = [
                c
                async for c in provider.generate(
                    LLMRequest((LLMMessage("system", "rules"),), max_tokens=100)
                )
            ]

    asyncio.run(run())


@pytest.mark.parametrize(
    "values",
    [{"max_tokens": 0}, {"max_tokens": 513}, {"temperature": float("nan")}, {"model": "other"}],
)
def test_invalid_generation_request_rejected_before_model_load(values):
    async def run():
        provider = Qwen3TransformersLLM(settings())
        with pytest.raises(ValueError):
            _ = [
                c
                async for c in provider.generate(
                    LLMRequest((LLMMessage("user", "hi"),), **values)
                )
            ]
        assert provider._model is None

    asyncio.run(run())


def test_8b_memory_admission_precedes_any_model_download(monkeypatch):
    import sys

    fake_torch = runtime()
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setitem(sys.modules, "transformers", SimpleNamespace())
    monkeypatch.setitem(
        sys.modules,
        "psutil",
        SimpleNamespace(virtual_memory=lambda: SimpleNamespace(available=8 * 1024**3)),
    )
    provider = Qwen3TransformersLLM(settings(local_llm_local_files_only=False))
    with pytest.raises(ProviderUnavailable, match="40 GiB"):
        provider._load()
    assert provider._model is None


def test_load_forwards_revision_dtype_cache_only_and_secret_at_boundary(monkeypatch):
    import sys

    calls = []
    fake_torch = runtime()
    fake_torch.float32 = "fp32-storage"
    fake_torch.float16 = "fp16-storage"
    fake_torch.bfloat16 = "bf16-storage"
    model = SimpleNamespace(
        config=SimpleNamespace(_commit_hash="resolved-revision"),
        to=lambda device: calls.append(("device", device)),
        eval=lambda: calls.append(("eval", True)),
    )
    tokenizer = Tokenizer()

    def load_tokenizer(model_id, **kwargs):
        calls.append(("tokenizer", kwargs))
        return tokenizer

    def load_model(model_id, **kwargs):
        calls.append(("model", kwargs))
        return model

    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setitem(
        sys.modules,
        "transformers",
        SimpleNamespace(
            AutoTokenizer=SimpleNamespace(from_pretrained=load_tokenizer),
            AutoModelForCausalLM=SimpleNamespace(from_pretrained=load_model),
        ),
    )
    provider = Qwen3TransformersLLM(
        settings(local_llm_model="synthetic-tiny", HF_TOKEN="synthetic-secret")
    )
    provider._load()
    provider._load()
    options = dict(calls)["model"]
    assert options["dtype"] == "fp32-storage"
    assert options["local_files_only"] is True
    assert options["trust_remote_code"] is False
    assert options["token"] == "synthetic-secret"
    assert provider._revision == "resolved-revision"
    assert len([c for c in calls if c[0] == "model"]) == 1
