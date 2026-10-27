"""Reproducible synthetic load harness; local inference requires explicit opt-in.

Default uses a fake model through the real ASGI boundary. Server CPU/RSS sampling
is optional (psutil, explicit server PID for local mode). VRAM is deliberately
null: unified Apple memory is not a separately measured GPU-memory capability.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
from collections import Counter
from contextlib import AsyncExitStack
from time import perf_counter
from typing import Any

import httpx
from pydantic import SecretStr

from roma.core.ai_contracts import AudioFormat, STTRequest, STTResult, TTSRequest, TTSResult
from roma.core.serving_settings import SpeechServingSettings
from roma.serving.app import create_speech_app
from roma.serving.protocol import stt_payload, tts_payload


class _ResourceSampler:
    def __init__(self, pid: int | None) -> None:
        self.cpu: float | None = None
        self.ram: int | None = None
        self.capability = "server_pid_not_supplied"
        self._process = None
        self._known: dict[int, Any] = {}
        self._psutil: Any = None
        self._self_resource: Any = None
        self._last_cpu: tuple[float, float] | None = None
        self._errors: tuple[type[Exception], ...] = (OSError, ValueError)
        if pid is not None:
            try:
                import psutil

                self._errors = (OSError, ValueError, psutil.Error)
                self._psutil = psutil
                self._process = psutil.Process(pid)
                self.capability = "process_tree_rss_sum_and_cpu_percent"
                self.sample()
            except ImportError:
                self.capability = "psutil_unavailable"
                if pid == os.getpid():
                    try:
                        import resource

                        self._self_resource = resource
                        self.capability = "self_rusage_cpu_percent_and_peak_rss"
                        self.sample()
                    except ImportError:
                        self.capability = "resource_sampling_unavailable"
            except self._errors:
                self.capability = "process_unavailable"

    def sample(self) -> None:
        if self._self_resource is not None:
            usage = self._self_resource.getrusage(self._self_resource.RUSAGE_SELF)
            now = perf_counter()
            cpu_time = usage.ru_utime + usage.ru_stime
            if self._last_cpu is not None:
                elapsed = now - self._last_cpu[0]
                cpu = (cpu_time - self._last_cpu[1]) / elapsed * 100 if elapsed > 0 else 0
                self.cpu = max(self.cpu or 0, cpu)
            self._last_cpu = (now, cpu_time)
            # macOS reports bytes; Linux/BSD commonly report KiB.
            self.ram = usage.ru_maxrss * (1 if sys.platform == "darwin" else 1024)
            return
        if self._process is None:
            return
        try:
            processes = [self._process, *self._process.children(recursive=True)]
            cpu, ram = 0.0, 0
            for process in processes:
                existing = self._known.setdefault(process.pid, process)
                cpu += existing.cpu_percent()
                ram += existing.memory_info().rss
            self.cpu = max(self.cpu or 0, cpu)
            self.ram = max(self.ram or 0, ram)
        except self._psutil.Error:
            self.capability = "process_sampling_incomplete"

    async def watch(self) -> None:
        while True:
            self.sample()
            await asyncio.sleep(0.02)


class _FakeSTT:
    async def transcribe(self, request: STTRequest) -> STTResult:
        await asyncio.sleep(0.01)
        return STTResult("synthetic capacity fixture", "hi")


class _FakeTTS:
    async def synthesize(self, request: TTSRequest) -> TTSResult:
        await asyncio.sleep(0.01)
        return TTSResult(b"\0\0" * 160, request.format, 20)


async def run_capacity(
    *,
    requests: int = 20,
    concurrency: int = 1,
    mode: str = "fake",
    allow_local_models: bool = False,
    settings: SpeechServingSettings | None = None,
    server_pid: int | None = None,
    operation: str = "stt",
) -> dict:
    if mode not in {"fake", "local"} or operation not in {"stt", "tts", "mixed"}:
        raise ValueError("Unknown capacity mode or operation")
    if (
        type(requests) is not int
        or not 1 <= requests <= 1000
        or type(concurrency) is not int
        or not 1 <= concurrency <= 32
    ):
        raise ValueError("Capacity requires 1–1000 requests and 1–32 workers")
    if mode == "local" and not allow_local_models:
        raise ValueError("Local inference requires explicit --allow-local-models authorization")
    if mode == "fake":
        settings = settings or SpeechServingSettings(  # type: ignore[call-arg]
            _env_file=None,
            speech_service_key=SecretStr("offline-capacity-key-0123456789abcdef"),
        )
    else:
        settings = settings or SpeechServingSettings()
    key = settings.require_key()
    sampler = _ResourceSampler(os.getpid() if mode == "fake" else server_pid)
    statuses: Counter[str] = Counter()
    latencies: list[float] = []
    stt_body = stt_payload(STTRequest(b"\0\0" * 16000, AudioFormat(16000), "hi"))
    tts_body = tts_payload(TTSRequest("नमस्ते।"))

    async with AsyncExitStack() as stack:
        transport = None
        if mode == "fake":

            async def warmup():
                pass

            app = create_speech_app(settings, stt=_FakeSTT(), tts=_FakeTTS(), warmup=warmup)
            await stack.enter_async_context(app.router.lifespan_context(app))
            transport = httpx.ASGITransport(app=app)
        client = await stack.enter_async_context(
            httpx.AsyncClient(
                transport=transport,
                base_url=settings.speech_service_url,
                trust_env=False,
                follow_redirects=False,
                timeout=settings.speech_service_timeout_secs,
                headers={"Authorization": "Bearer " + key},
                limits=httpx.Limits(
                    max_connections=concurrency, max_keepalive_connections=concurrency
                ),
            )
        )
        ready = await client.get("/ready")
        if ready.status_code != 200:
            raise ValueError("Speech service is not ready")
        queue: asyncio.Queue[int] = asyncio.Queue()
        for index in range(requests):
            queue.put_nowait(index)

        async def worker():
            while not queue.empty():
                index = queue.get_nowait()
                selected = (
                    "tts"
                    if operation == "tts" or (operation == "mixed" and index % 2)
                    else "stt"
                )
                started = perf_counter()
                try:
                    async with asyncio.timeout(settings.speech_service_timeout_secs):
                        async with client.stream(
                            "POST",
                            "/v1/" + selected,
                            json=tts_body if selected == "tts" else stt_body,
                        ) as response:
                            size = 0
                            async for chunk in response.aiter_bytes():
                                size += len(chunk)
                                if size > settings.speech_service_max_body_bytes:
                                    raise ValueError("Response exceeds capacity byte bound")
                            statuses[str(response.status_code)] += 1
                except (TimeoutError, httpx.HTTPError, ValueError):
                    statuses["transport_error"] += 1
                latencies.append((perf_counter() - started) * 1000)

        watch = asyncio.create_task(sampler.watch())
        started = perf_counter()
        try:
            await asyncio.gather(*(worker() for _ in range(concurrency)))
        finally:
            elapsed = perf_counter() - started
            sampler.sample()
            watch.cancel()
            await asyncio.gather(watch, return_exceptions=True)
    latencies.sort()

    def percentile(percent: float) -> float:
        return latencies[max(0, math.ceil(len(latencies) * percent) - 1)]

    success = statuses["200"]
    return {
        "schema_version": 1,
        "evidence_mode": mode,
        "operation": operation,
        "requests": requests,
        "concurrency": concurrency,
        "successes": success,
        "errors": requests - success,
        "error_rate": (requests - success) / requests,
        "status_counts": dict(statuses),
        "elapsed_secs": elapsed,
        "throughput_success_per_sec": success / elapsed,
        "latency_ms": {
            "p50": percentile(0.50),
            "p95": percentile(0.95),
            "p99": percentile(0.99),
        },
        "latency_population": "all_attempts_including_rejections",
        "peak_cpu_percent": sampler.cpu,
        "peak_ram_bytes": sampler.ram,
        "resource_scope": (
            "fake_harness_process"
            if sampler._self_resource is not None
            else "fake_harness_process_tree"
        )
        if mode == "fake"
        else "explicit_server_process_tree",
        "resource_capability": sampler.capability,
        "peak_vram_bytes": None,
        "vram_capability": "unavailable_not_inferred",
        "asr_batch_mode": "serialized_http_requests_existing_internal_chunks_unchanged",
        "quality_gate_passed": False,
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("fake", "local"), default="fake")
    parser.add_argument("--allow-local-models", action="store_true")
    parser.add_argument("--requests", type=int, default=20)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--operation", choices=("stt", "tts", "mixed"), default="stt")
    parser.add_argument("--server-pid", type=int)
    args = parser.parse_args(argv)
    report = asyncio.run(run_capacity(**vars(args)))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
