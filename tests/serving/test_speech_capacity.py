import asyncio

import pytest


def test_capacity_fake_report_counts_overload_and_does_not_invent_gpu_capacity():
    from roma.serving.capacity import run_capacity

    report = asyncio.run(run_capacity(requests=8, concurrency=2))
    assert report["evidence_mode"] == "fake"
    assert report["successes"] + report["errors"] == 8
    assert report["successes"] > 0
    assert report["errors"] > 0
    assert report["status_counts"]["429"] > 0
    assert report["throughput_success_per_sec"] > 0
    assert report["latency_ms"]["p95"] >= report["latency_ms"]["p50"] >= 0
    assert report["peak_vram_bytes"] is None
    assert report["vram_capability"] == "unavailable_not_inferred"
    assert report["quality_gate_passed"] is False


def test_capacity_local_mode_requires_explicit_model_inference_permission():
    from roma.serving.capacity import run_capacity

    with pytest.raises(ValueError, match="explicit"):
        asyncio.run(run_capacity(mode="local"))
