import asyncio
import json
from pathlib import Path

import pytest


def test_retrieval_metrics_use_all_answerable_cases_and_actual_rank():
    from roma.eval.rag_benchmark import summarize

    rows = [
        {
            "case_id": "a",
            "relevant": ["x", "y"],
            "retrieved": ["wrong", "x"],
            "expected_deferral": False,
            "fallback": False,
            "span_supported": True,
            "safe": True,
            "retrieval_ms": 10,
            "route_ok": True,
            "safety_case": False,
        },
        {
            "case_id": "b",
            "relevant": ["z"],
            "retrieved": [],
            "expected_deferral": False,
            "fallback": True,
            "span_supported": False,
            "safe": True,
            "retrieval_ms": 30,
            "route_ok": True,
            "safety_case": False,
        },
        {
            "case_id": "c",
            "relevant": [],
            "retrieved": [],
            "expected_deferral": True,
            "fallback": True,
            "span_supported": False,
            "safe": True,
            "retrieval_ms": None,
            "route_ok": True,
            "safety_case": True,
        },
    ]
    result = summarize(rows)
    assert result["recall_at_5"] == 0.25
    assert result["mrr"] == 0.25
    assert result["grounded_span_support"] == 1
    assert result["deferral_accuracy"] == 1
    assert result["retrieval_p95_ms"] == 30
    assert result["real_claude_groundedness"] is None
    assert result["counts"]["answerable"] == 2


def test_empty_generation_does_not_fabricate_a_grounding_score():
    from roma.eval.rag_benchmark import summarize

    result = summarize([])
    assert result["grounded_span_support"] is None
    assert result["recall_at_5"] is None
    assert result["retrieval_p95_ms"] is None


def test_checked_golden_runs_through_routing_search_and_answer_services_offline():
    from roma.eval.rag_benchmark import load_golden, run_mock_benchmark

    golden = load_golden(Path(__file__).resolve().parents[2] / "benchmarks/rag/golden-v1.json")
    report = asyncio.run(run_mock_benchmark(golden))
    assert report["complete"]
    assert len(report["cases"]) == len(golden["cases"])
    assert report["generation_evaluator"] == "deterministic_fixture_selector"
    assert report["embedding_inference_executed"] is False
    assert report["pgvector_executed"] is False
    assert report["quality_gate_passed"] is False
    assert report["summary"]["real_claude_groundedness"] is None
    heldout = [row for row in report["cases"] if row["split"] == "heldout"]
    assert report["summary"]["route_accuracy"] == sum(row["route_ok"] for row in heldout) / len(
        heldout
    )
    assert report["summary"]["grounded_span_support"] == 1
    assert report["summary"]["deferral_accuracy"] == 1
    assert report["summary"]["safety_accuracy"] == 1
    assert {row["input_language"] for row in report["cases"]} == {"gu", "hi", "en", "hi-en"}
    assert all("query" not in row and "answer" not in row for row in report["cases"])


@pytest.mark.parametrize(
    "url",
    [
        "",
        "postgresql+asyncpg://user@127.0.0.1/roma",
        "postgresql+asyncpg://user@db.example.invalid/roma_rag_benchmark_trial",
        "sqlite:///roma_rag_benchmark_trial",
        "postgresql+asyncpg://user@127.0.0.1/roma_rag_benchmark_",
    ],
)
def test_cli_guard_rejects_working_or_nonlocal_database_before_writes(url):
    from roma.eval.rag_benchmark import guard_test_database_url

    with pytest.raises(ValueError, match="TEST ONLY"):
        guard_test_database_url(url, test_only=True)


def test_cli_guard_needs_explicit_test_only_and_material_changes_invalidate_report():
    from roma.core.ai_contracts import EmbeddingIdentity
    from roma.eval.rag_benchmark import guard_test_database_url, material_fingerprint

    url = "postgresql+asyncpg://user@127.0.0.1/roma_rag_benchmark_trial"
    with pytest.raises(ValueError):
        guard_test_database_url(url, test_only=False)
    assert guard_test_database_url(url, test_only=True) == url
    golden = {"cases": ["synthetic"]}
    identity = EmbeddingIdentity("fixture", "1", 2)
    first = material_fingerprint(golden, identity, threshold=0.8)
    assert material_fingerprint(golden, identity, threshold=0.85) != first
    assert (
        material_fingerprint(golden, identity, threshold=0.8, claude_model="changed") != first
    )
    assert (
        material_fingerprint(golden, EmbeddingIdentity("fixture", "2", 2), threshold=0.8)
        != first
    )


def test_threshold_selection_never_uses_heldout_observations():
    from roma.eval.rag_benchmark import select_calibrated_threshold

    answer = {
        "split": "calibration",
        "relevant": ["a"],
        "retrieved": ["a"],
        "expected_deferral": False,
    }
    absent = {
        "split": "calibration",
        "relevant": [],
        "retrieved": [],
        "expected_deferral": True,
    }
    result = select_calibrated_threshold([(0.7, [answer, absent]), (0.8, [answer, absent])])
    assert result["selected_threshold"] == 0.8
    with pytest.raises(ValueError, match="heldout"):
        select_calibrated_threshold([(0.8, [answer | {"split": "heldout"}])])


def test_completed_report_converts_actual_aggregates_without_claude_scores(tmp_path):
    from roma.eval.rag_benchmark import benchmark_evidence, load_golden, run_mock_benchmark

    golden = load_golden(Path(__file__).resolve().parents[2] / "benchmarks/rag/golden-v1.json")
    report = asyncio.run(run_mock_benchmark(golden))
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report))
    evidence = benchmark_evidence(report, path)
    assert evidence.models[0].task == "embedding"
    assert evidence.config["real_claude_evaluated"] is False
    assert all(metric.metric != "real_claude_groundedness" for metric in evidence.metrics)
    assert evidence.sample_count == len(golden["cases"])
    assert evidence.failure_artifacts[0].path == str(path)
    assert all(metric.sample_count > 0 for metric in evidence.metrics)
