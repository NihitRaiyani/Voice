"""Optional Apple-silicon INT4 inference behind the existing LLMProvider."""

from __future__ import annotations

import asyncio
import copy
import json
import math
import platform
import queue
import threading
import time
from collections import OrderedDict
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from roma.core.local_llm_config import LocalLLMSettings
from roma.providers.ai.contracts import (
    LLMChunk,
    LLMGenerationMetrics,
    LLMRequest,
    ProviderUnavailable,
)


class Qwen3MLXLLM:
    """Serialized streaming with bounded prompts and cooperative token-boundary stop.

    Model loading and decoding stay on one dedicated worker for the adapter lifetime. Cancellation waits for
    that worker before releasing the model; a GPU forward pass cannot be preempted.
    Construction imports no optional SDK and never downloads weights.
    """

    def __init__(self, settings: LocalLLMSettings | None = None) -> None:
        self.settings = settings or LocalLLMSettings()
        self._lock = asyncio.Lock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="roma-mlx")
        self._model: Any = None
        self._tokenizer: Any = None
        self._stream: Any = None
        self._sampler: Any = None
        self._mx: Any = None
        self._make_cache: Any = None
        # Immutable system prefixes only: four bounded entries, never transcripts.
        self._prefixes: OrderedDict[tuple, Any] = OrderedDict()
        self._load_secs = 0.0
        self._revision = self.settings.local_llm_revision

    def _load(self) -> None:
        if self._model is not None:
            return
        if platform.system() != "Darwin" or platform.machine() != "arm64":
            raise ProviderUnavailable("The MLX profile requires an Apple-silicon Mac")
        if self.settings.local_llm_device not in {"auto", "mps"}:
            raise ProviderUnavailable("The MLX profile requires the Apple GPU (mps/auto)")
        if self.settings.local_llm_dtype != "int4":
            raise ProviderUnavailable("This MLX adapter requires a pre-quantized INT4 model")
        try:
            import mlx.core as mx
            from huggingface_hub import snapshot_download
            from mlx_lm import load, stream_generate
            from mlx_lm.models.cache import make_prompt_cache
            from mlx_lm.sample_utils import make_sampler
        except ImportError:
            raise ProviderUnavailable(
                "Install the Apple runtime: make local-llm-mac-sync"
            ) from None
        started = time.perf_counter()
        try:
            path = snapshot_download(
                self.settings.local_llm_model,
                revision=self.settings.local_llm_revision,
                local_files_only=self.settings.local_llm_local_files_only,
                token=self.settings.hf_token.get_secret_value() or False,
                allow_patterns=["*.json", "*.safetensors", "*.jinja", "merges.txt"],
            )
            # Check the stored format before materializing weights.
            quant = json.loads((Path(path) / "config.json").read_text()).get("quantization")
            if not quant or quant.get("bits") != 4:
                raise ProviderUnavailable("Selected MLX model is not 4-bit quantized")
            loaded = load(path, tokenizer_config={"trust_remote_code": False})
            model, tokenizer = loaded[0], loaded[1]
            if not getattr(tokenizer, "chat_template", None):
                raise ProviderUnavailable(
                    "Model chat template is missing; rerun the pinned download including *.jinja data"
                )
            mx.eval(model.parameters())
        except ProviderUnavailable:
            raise
        except Exception:  # noqa: BLE001 — SDK errors can contain secrets/URLs
            raise ProviderUnavailable(
                "MLX model loading failed; check pinned cache and runtime"
            ) from None
        self._revision = Path(path).name
        self._model, self._tokenizer = model, tokenizer
        self._stream, self._sampler = stream_generate, make_sampler
        self._mx, self._make_cache = mx, make_prompt_cache
        self._load_secs = time.perf_counter() - started

    def _cached_prefix(self, request, messages, token_ids, check_prefill):
        if (
            not self.settings.local_llm_prefix_cache
            or request.metadata.get("cache_prefix") != "system-v1"
            or messages[0]["role"] != "system"
            or self._make_cache is None
        ):
            return token_ids, None, 0, False
        # Token identity matters: separately encoded text may merge at a boundary.
        prefix = self._tokenizer.apply_chat_template(
            messages[:1],
            tokenize=False,
            add_generation_prompt=False,
            enable_thinking=False,
        )
        prefix_ids = self._tokenizer.encode(prefix)
        if (
            not prefix_ids
            or token_ids[: len(prefix_ids)] != prefix_ids
            or len(prefix_ids) >= len(token_ids)
        ):
            return token_ids, None, 0, False
        key = (self.settings.local_llm_model, self._revision, tuple(prefix_ids))
        hit = key in self._prefixes
        if not hit:
            cache = self._make_cache(self._model)
            for start in range(0, len(prefix_ids), 256):
                check_prefill(start, len(prefix_ids))
                self._model(self._mx.array([prefix_ids[start : start + 256]]), cache=cache)
                self._mx.eval([entry.state for entry in cache])
            check_prefill(len(prefix_ids), len(prefix_ids))
            self._prefixes[key] = cache
            if len(self._prefixes) > 4:
                self._prefixes.popitem(last=False)
        self._prefixes.move_to_end(key)
        # Generation mutates its cache. A fresh copy prevents any caller suffix,
        # cancellation or generated token from contaminating the reusable prefix.
        return (
            token_ids[len(prefix_ids) :],
            copy.deepcopy(self._prefixes[key]),
            len(prefix_ids),
            hit,
        )

    def _run(self, request: LLMRequest, maximum: int, stop, events) -> None:
        try:
            self._load()
            if stop.is_set():
                return
            messages = [{"role": m.role, "content": m.content} for m in request.messages]
            prompt = self._tokenizer.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True, enable_thinking=False
            )
            token_ids = self._tokenizer.encode(prompt)
            if len(token_ids) > self.settings.local_llm_max_prompt_tokens:
                events.put(
                    ValueError(
                        "prompt exceeds local token budget; refusing to truncate system rules"
                    )
                )
                return
            context = getattr(self._model.args, "max_position_embeddings", None)
            if context and len(token_ids) + maximum > context:
                events.put(ValueError("prompt plus completion exceeds model context"))
                return
            started = time.perf_counter()
            first = None
            count = 0
            finish = "length"

            def check_prefill(processed: int, total: int) -> None:
                if stop.is_set():
                    raise ProviderUnavailable("Local generation stopped")
                if (
                    time.perf_counter() - started
                    > self.settings.local_llm_generation_timeout_secs
                ):
                    raise ProviderUnavailable("Local generation exceeded its time budget")

            suffix, prefix_cache, cached_tokens, cache_hit = self._cached_prefix(
                request,
                messages,
                token_ids,
                check_prefill,
            )
            cache_options = {"prompt_cache": prefix_cache} if prefix_cache is not None else {}
            stream = self._stream(
                self._model,
                self._tokenizer,
                suffix,
                max_tokens=maximum,
                sampler=self._sampler(temp=request.temperature, top_p=0.8, top_k=20),
                prefill_step_size=256,
                prompt_progress_callback=check_prefill,
                **cache_options,
            )
            try:
                for result in stream:
                    now = time.perf_counter()
                    if stop.is_set():
                        return
                    if now - started > self.settings.local_llm_generation_timeout_secs:
                        raise ProviderUnavailable("Local generation exceeded its time budget")
                    first = first or now
                    count = result.generation_tokens
                    if result.text:
                        events.put(LLMChunk(result.text))
                    if result.finish_reason:
                        finish = "stop" if result.finish_reason == "stop" else "length"
            finally:
                stream.close()
            elapsed = time.perf_counter() - started
            events.put(
                LLMChunk(
                    "",
                    finish,
                    LLMGenerationMetrics(
                        model=self.settings.local_llm_model,
                        revision=self._revision,
                        device="mps",
                        dtype="int4",
                        load_secs=self._load_secs,
                        prompt_tokens=len(token_ids),
                        generated_tokens=count,
                        ttft_ms=(first - started) * 1000 if first else None,
                        generation_secs=elapsed,
                        tokens_per_second=count / elapsed if elapsed else 0,
                        cached_prompt_tokens=cached_tokens if cache_hit else 0,
                        prefix_cache_hit=cache_hit,
                        peak_mlx_bytes=int(getattr(result, "peak_memory", 0) * 1e9),
                    ),
                )
            )
        except ProviderUnavailable as exc:
            events.put(exc)
        except Exception:  # noqa: BLE001 — never expose raw SDK exception text
            events.put(
                ProviderUnavailable("Local MLX generation failed; check runtime and hardware")
            )
        finally:
            events.put(None)

    async def generate(self, request: LLMRequest) -> AsyncIterator[LLMChunk]:
        if not request.messages:
            raise ValueError("LLM messages must not be empty")
        if request.model not in {None, self.settings.local_llm_model}:
            raise ValueError("request model must match the configured local model")
        if not math.isfinite(request.temperature) or not 0 <= request.temperature <= 2:
            raise ValueError("temperature must be finite and between 0 and 2")
        maximum = (
            request.max_tokens
            if request.max_tokens is not None
            else self.settings.local_llm_max_new_tokens
        )
        if not 1 <= maximum <= 512:
            raise ValueError("max_tokens must be between 1 and 512")
        async with self._lock:
            stop = threading.Event()
            events: queue.Queue = queue.Queue()
            worker = asyncio.wrap_future(
                self._executor.submit(self._run, request, maximum, stop, events)
            )
            try:
                while True:
                    try:
                        event = await asyncio.to_thread(events.get, True, 0.1)
                    except queue.Empty:
                        continue
                    if event is None:
                        break
                    if isinstance(event, Exception):
                        raise event
                    yield event
            finally:
                stop.set()
                try:
                    await asyncio.shield(worker)
                except asyncio.CancelledError:
                    await worker
                    raise
