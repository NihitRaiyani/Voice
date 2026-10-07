import json
from dataclasses import asdict
from pathlib import Path

import pytest
from roma.eval.local_comparison import latency_summary, summarize_candidate
from roma.eval.local_language import lab_request


def artifact():
    cases = json.loads(
        (Path(__file__).resolve().parents[2] / "benchmarks/llm/language_cases.json").read_text()
    )
    rows = [
        {
            "id": case["id"],
            "language": case["language"],
            "prompt": [asdict(m) for m in lab_request(case).messages],
            "raw_output": "synthetic",
            "finish_reason": "stop",
            "generation": {
                "max_tokens": 256,
                "temperature": 0.7,
                "prompt_version": "compact-v1",
                "sampling_policy": "qwen-nonthinking-p08-k20",
            },
            "metrics": [
                {
                    "model": "candidate",
                    "revision": "a" * 40,
                    "generated_tokens": 10,
                    "prompt_tokens": 350,
                    "ttft_ms": index + 1,
                    "prefix_cache_hit": index > 0,
                }
            ],
        }
        for index, case in enumerate(cases)
    ]
    return {"model": "candidate", "revision": "a" * 40, "results": rows}, cases


def test_summary_is_descriptive_and_distinguishes_cache_hits():
    report, cases = artifact()
    summary = summarize_candidate(report, cases)
    assert summary["ttft"] == {"samples": 40, "p50_ms": 20.5, "p95_ms": 38}
    assert summary["cache_hits"] == 39
    assert summary["voice_approval"] == "deferred"
    assert summary["native_scoring"] == "deferred"
    assert latency_summary([])["p95_ms"] is None


@pytest.mark.parametrize(
    "fault", ["missing", "duplicate", "stale", "metrics", "shifted-metrics", "identity"]
)
def test_comparison_refuses_incomplete_or_stale_measurements(fault):
    report, cases = artifact()
    if fault == "missing":
        report["results"].pop()
    elif fault == "duplicate":
        report["results"][-1] = report["results"][0]
    elif fault == "stale":
        report["results"][0]["prompt"][0]["content"] += "old"
    elif fault == "metrics":
        report["results"][0]["metrics"] = []
    elif fault == "shifted-metrics":
        report["results"][1]["metrics"] += report["results"][0]["metrics"]
        report["results"][0]["metrics"] = []
    else:
        report["model"] = "other"
    with pytest.raises(ValueError):
        summarize_candidate(report, cases)
