"""Direct Transformers decoding behind LLMProvider. Optional SDKs load lazily."""

from __future__ import annotations

import asyncio
import math
import queue
import threading
import time
from collections.abc import AsyncIterator
from typing import Any

from roma.core.local_llm_config import LocalLLMSettings
from roma.providers.ai.contracts import (
    LLMChunk,
    LLMGenerationMetrics,
    LLMRequest,
    ProviderUnavailable,
)


def select_runtime(torch: Any, device: str, dtype: str) -> tuple[str, str]:
    if device == "auto":
        device = (
            "cuda"
            if torch.cuda.is_available()
            else ("mps" if torch.backends.mps.is_available() else "cpu")
        )
    if device == "cuda" and not torch.cuda.is_available():
        raise ProviderUnavailable("CUDA is unavailable; choose a supported device")
    if device == "mps" and not torch.backends.mps.is_available():
        raise ProviderUnavailable("MPS is unavailable; choose a supported device")
    if dtype == "auto":
        dtype = (
            "bf16"
            if device == "cuda" and torch.cuda.is_bf16_supported()
            else ("fp16" if device != "cpu" else "fp32")
        )
    if dtype == "int4" and device != "cuda":
        raise ProviderUnavailable("This INT4 adapter requires CUDA and bitsandbytes")
    if dtype == "bf16" and (
        device == "mps" or (device == "cuda" and not torch.cuda.is_bf16_supported())
    ):
        raise ProviderUnavailable("BF16 is unsupported by this adapter/device; use FP16")
    if device == "cpu" and dtype == "fp16":
        raise ProviderUnavailable("Use FP32 or BF16 on CPU; FP16 CPU is not supported here")
    return device, dtype


class Qwen3TransformersLLM:
    """One loaded model per adapter; serialized requests, stoppable worker decoding.

    Cancellation/timeout stops at the next decoding boundary and joins the worker
    before releasing the model. A running GPU/CPU forward pass cannot be preempted.
    No Transformers, Torch or Hugging Face import/download occurs on construction.
    """

    def __init__(self, settings: LocalLLMSettings | None = None) -> None:
        self.settings = settings or LocalLLMSettings()
        self._lock = asyncio.Lock()
        self._model: Any = None
        self._tokenizer: Any = None
        self._torch: Any = None
        self._transformers: Any = None
        self._device = ""
        self._dtype = ""
        self._load_secs = 0.0
        self._revision = self.settings.local_llm_revision

    def _load(self) -> None:
        if self._model is not None:
            return
        try:
            import torch
            import transformers
        except ImportError:
            raise ProviderUnavailable(
                "Install the locked local runtime: make local-llm-sync"
            ) from None
        self._torch, self._transformers = torch, transformers
        device, dtype = select_runtime(
            torch,
            self.settings.local_llm_device,
            self.settings.local_llm_dtype,
        )
        # Refuse an 8B download/load that cannot fit; reserve room for activations/KV/OS.
        if self.settings.local_llm_model == "Qwen/Qwen3-8B":
            import psutil

            available = (
                torch.cuda.mem_get_info()[0]
                if device == "cuda"
                else psutil.virtual_memory().available
            )
            required_gib = 8 if dtype == "int4" else (40 if dtype == "fp32" else 20)
            if available < required_gib * 1024**3:
                raise ProviderUnavailable(
                    f"Qwen3-8B {dtype} requires at least {required_gib} GiB free "
                    "for this lab; use a suitable host or explicitly evaluate a smaller model"
                )
        started = time.perf_counter()
        options: dict[str, Any] = {
            "revision": self.settings.local_llm_revision,
            "local_files_only": self.settings.local_llm_local_files_only,
            "trust_remote_code": False,
            "token": self.settings.hf_token.get_secret_value() or False,
        }
        try:
            tokenizer = transformers.AutoTokenizer.from_pretrained(
                self.settings.local_llm_model,
                **options,
            )
            model_options = dict(options)
            if dtype == "int4":
                try:
                    import bitsandbytes  # noqa: F401
                except ImportError:
                    raise ProviderUnavailable(
                        "Install the local-llm-int4 extra on a CUDA host"
                    ) from None
                compute = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
                model_options.update(
                    device_map={"": 0},
                    quantization_config=transformers.BitsAndBytesConfig(
                        load_in_4bit=True,
                        bnb_4bit_quant_type="nf4",
                        bnb_4bit_compute_dtype=compute,
                        bnb_4bit_use_double_quant=True,
                    ),
                )
            else:
                model_options["dtype"] = {
                    "bf16": torch.bfloat16,
                    "fp16": torch.float16,
                    "fp32": torch.float32,
                }[dtype]
            model: Any = transformers.AutoModelForCausalLM.from_pretrained(
                self.settings.local_llm_model,
                **model_options,
            )
            if dtype != "int4":
                model.to(device)
            model.eval()
        except ProviderUnavailable:
            raise
        except Exception:  # noqa: BLE001 — SDK errors may contain secrets/paths/URLs
            raise ProviderUnavailable(
                "Local model loading failed; check cache/revision, runtime and hardware "
                "(downloads require LOCAL_LLM_LOCAL_FILES_ONLY=false)"
            ) from None
        self._model, self._tokenizer = model, tokenizer
        self._device, self._dtype = device, dtype
        self._load_secs = time.perf_counter() - started
        self._revision = getattr(model.config, "_commit_hash", None) or self._revision

    async def generate(self, request: LLMRequest) -> AsyncIterator[LLMChunk]:
        if not request.messages:
            raise ValueError("LLM messages must not be empty")
        if request.model not in (None, self.settings.local_llm_model):
            raise ValueError("request model must match the configured local model")
        if not math.isfinite(request.temperature) or not 0 <= request.temperature <= 2:
            raise ValueError("temperature must be finite and between 0 and 2")
        max_tokens = (
            request.max_tokens
            if request.max_tokens is not None
            else self.settings.local_llm_max_new_tokens
        )
        if not 1 <= max_tokens <= 512:
            raise ValueError("max_tokens must be between 1 and 512")
        async with self._lock:
            # Shield loading so cancellation cannot release a partially-loading model.
            load_task = asyncio.create_task(asyncio.to_thread(self._load))
            try:
                await asyncio.shield(load_task)
            except asyncio.CancelledError:
                await load_task
                raise
            torch, tf, tokenizer = self._torch, self._transformers, self._tokenizer
            messages = [{"role": m.role, "content": m.content} for m in request.messages]
            prompt = tokenizer.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=True,
                enable_thinking=False,
            )
            inputs = tokenizer(prompt, return_tensors="pt", return_token_type_ids=False).to(
                self._model.device
            )
            prompt_tokens = inputs["input_ids"].shape[-1]
            if prompt_tokens > self.settings.local_llm_max_prompt_tokens:
                raise ValueError(
                    "prompt exceeds local token budget; refusing to truncate system rules"
                )
            context_limit = getattr(self._model.config, "max_position_embeddings", None)
            if context_limit and prompt_tokens + max_tokens > context_limit:
                raise ValueError("prompt plus completion exceeds model context")
            stop = threading.Event()
            errors: list[Exception] = []
            first_token: list[float] = []
            generated_tokens: list[int] = []
            started = time.perf_counter()
            deadline = started + self.settings.local_llm_generation_timeout_secs

            class TimedStreamer(tf.TextIteratorStreamer):  # type: ignore[name-defined]  # lazy optional SDK base
                def put(self, value: Any) -> None:
                    if not self.next_tokens_are_prompt and not first_token:
                        first_token.append(time.perf_counter())
                    super().put(value)

            class StopDecoding(tf.StoppingCriteria):  # type: ignore[name-defined]  # lazy optional SDK base
                def __call__(self, input_ids: Any, scores: Any, **kwargs: Any) -> bool:
                    return stop.is_set() or time.perf_counter() >= deadline

            streamer = TimedStreamer(
                tokenizer, skip_prompt=True, skip_special_tokens=True, timeout=0.1
            )
            options = {
                **inputs,
                "max_new_tokens": max_tokens,
                "do_sample": request.temperature > 0,
                "streamer": streamer,
                "stopping_criteria": tf.StoppingCriteriaList([StopDecoding()]),
                "pad_token_id": tokenizer.pad_token_id
                if tokenizer.pad_token_id is not None
                else tokenizer.eos_token_id,
            }
            if request.temperature > 0:
                options.update(temperature=request.temperature, top_p=0.8, top_k=20)
            if self._device == "cuda":
                torch.cuda.reset_peak_memory_stats()

            def decode() -> None:
                try:
                    with torch.inference_mode():
                        result = self._model.generate(**options)
                    generated_tokens.append(result.shape[-1] - prompt_tokens)
                except Exception as exc:  # noqa: BLE001 — forward failure, never expose SDK details
                    errors.append(exc)
                    streamer.end()

            worker = threading.Thread(target=decode, daemon=True)
            worker.start()

            def next_text() -> tuple[bool, str]:
                try:
                    return False, next(streamer)
                except StopIteration:
                    return True, ""
                except queue.Empty:
                    return False, ""

            try:
                while True:
                    if time.perf_counter() >= deadline:
                        stop.set()
                        raise ProviderUnavailable("Local generation timed out")
                    done, text = await asyncio.to_thread(next_text)
                    if done:
                        break
                    if text:
                        yield LLMChunk(text)
            finally:
                stop.set()
                await asyncio.to_thread(worker.join)
            if errors:
                raise ProviderUnavailable("Local generation failed") from None
            if time.perf_counter() >= deadline:
                raise ProviderUnavailable("Local generation timed out")
            duration = time.perf_counter() - started
            count = generated_tokens[0]
            metrics = LLMGenerationMetrics(
                model=self.settings.local_llm_model,
                revision=self._revision,
                device=self._device,
                dtype=self._dtype,
                load_secs=self._load_secs,
                prompt_tokens=prompt_tokens,
                generated_tokens=count,
                ttft_ms=(first_token[0] - started) * 1000 if first_token else None,
                generation_secs=duration,
                tokens_per_second=count / duration,
                peak_cuda_bytes=torch.cuda.max_memory_allocated()
                if self._device == "cuda"
                else None,
            )
            yield LLMChunk("", "length" if count >= max_tokens else "stop", metrics)
