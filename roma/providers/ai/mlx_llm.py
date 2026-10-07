"""Optional Apple-silicon INT4 inference behind the existing LLMProvider."""

from __future__ import annotations

import asyncio
import json
import math
import platform
import queue
import threading
import time
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
                allow_patterns=["*.json", "*.safetensors", "merges.txt"],
            )
            # Check the stored format before materializing weights.
            quant = json.loads((Path(path) / "config.json").read_text()).get("quantization")
            if not quant or quant.get("bits") != 4:
                raise ProviderUnavailable("Selected MLX model is not 4-bit quantized")
            loaded = load(path, tokenizer_config={"trust_remote_code": False})
            model, tokenizer = loaded[0], loaded[1]
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
        self._load_secs = time.perf_counter() - started

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

            stream = self._stream(
                self._model,
                self._tokenizer,
                token_ids,
                max_tokens=maximum,
                sampler=self._sampler(temp=request.temperature),
                prefill_step_size=256,
                prompt_progress_callback=check_prefill,
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
