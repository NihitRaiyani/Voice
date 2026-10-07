"""Descriptive text-only measurements; never assigns native grades or approves voice."""

import math
from dataclasses import asdict
from statistics import median

from roma.eval.local_language import lab_request


def latency_summary(values: list[float]) -> dict:
    if not values:
        return {"samples": 0, "p50_ms": None, "p95_ms": None}
    ordered = sorted(values)
    return {
        "samples": len(values),
        "p50_ms": round(median(values), 2),
        "p95_ms": round(ordered[math.ceil(len(ordered) * 0.95) - 1], 2),
    }


def summarize_candidate(artifact: dict, cases: list[dict]) -> dict:
    rows = artifact["results"]
    expected = {case["id"]: case for case in cases}
    if len(rows) != len(expected) or {row["id"] for row in rows} != set(expected):
        raise ValueError("Comparison requires every case exactly once")
    for row in rows:
        case = expected[row["id"]]
        request = lab_request(case)
        generation = {
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
            "prompt_version": request.metadata.get("prompt_version"),
            "sampling_policy": request.metadata.get("sampling_policy"),
        }
        if row.get("generation") != generation or row.get("finish_reason") not in {
            "stop",
            "length",
        }:
            raise ValueError("Comparison has stale generation settings or no completion status")
        if row.get("language") != case["language"] or row.get("prompt") != [
            asdict(m) for m in lab_request(case).messages
        ]:
            raise ValueError("Comparison artifact has stale prompts or language")
    if any(len(row.get("metrics", [])) != 1 for row in rows):
        raise ValueError("Comparison requires one actual generation measurement per case")
    metrics = [row["metrics"][0] for row in rows]
    if any(
        not m.get("generated_tokens") or m.get("ttft_ms") is None for m in metrics
    ):
        raise ValueError("Comparison requires one actual generation measurement per case")
    identities = {(m["model"], m["revision"]) for m in metrics}
    if identities != {(artifact["model"], artifact["revision"])}:
        raise ValueError("Generation identity does not match candidate")
    times = [m["ttft_ms"] for m in metrics]
    return {
        "model": artifact["model"],
        "revision": artifact["revision"],
        "cases": len(rows),
        "prompt_tokens_min": min(m["prompt_tokens"] for m in metrics),
        "prompt_tokens_max": max(m["prompt_tokens"] for m in metrics),
        "ttft": latency_summary(times),
        "cached_ttft": latency_summary(
            [m["ttft_ms"] for m in metrics if m.get("prefix_cache_hit")]
        ),
        "uncached_ttft": latency_summary(
            [m["ttft_ms"] for m in metrics if not m.get("prefix_cache_hit")]
        ),
        "per_language_ttft": {
            lang: latency_summary(
                [row["metrics"][0]["ttft_ms"] for row in rows if row["language"] == lang]
            )
            for lang in sorted({row["language"] for row in rows})
        },
        "max_mlx_allocator_bytes": max(
            (m.get("peak_mlx_bytes") or 0 for m in metrics), default=0
        ),
        "cache_hits": sum(bool(m.get("prefix_cache_hit")) for m in metrics),
        "budget_exhausted_replies": sum(row.get("finish_reason") == "length" for row in rows),
        "unicode_replacement_replies": sum("\ufffd" in row["raw_output"] for row in rows),
        "resources": artifact.get("resources", {}),
        "status": "development-comparison-only",
        "native_scoring": "deferred",
        "voice_approval": "deferred",
    }
