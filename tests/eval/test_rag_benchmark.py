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
    assert result["retrieval_abstention_accuracy"] is None
    assert result["negative_query_retrieval_failure_rate"] is None
    assert result["failed_negative_query_ids"] == []
    assert result["counts"]["failed_negative_queries"] == 0
    assert result["search_executed_negative_query_failure_rate"] is None
    assert result["search_executed_failed_negative_query_ids"] == []
    assert result["counts"]["search_executed_negative_queries"] == 0
    assert result["counts"]["search_executed_failed_negative_queries"] == 0


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
    assert {metric.metric for metric in evidence.metrics} >= {
        "retrieval_abstention_accuracy",
        "negative_query_retrieval_failure_rate",
        "search_executed_negative_query_failure_rate",
    }
    for summary in [report["summary"], *report["language_summaries"].values()]:
        for key in (
            "retrieval_abstention_accuracy",
            "negative_query_retrieval_failure_rate",
            "search_executed_negative_query_failure_rate",
        ):
            summary.pop(key)
    assert benchmark_evidence(report, path).sample_count == len(golden["cases"])


def test_benchmark_thresholds_follow_the_canonical_matrix_without_claiming_groundedness():
    from roma.eval.project_readiness import load_matrix
    from roma.eval.rag_benchmark import THRESHOLDS, thresholds_from_matrix

    matrix = load_matrix()
    assert (
        thresholds_from_matrix(matrix)
        == THRESHOLDS
        == {
            "recall_at_5": 0.80,
            "mrr": 0.70,
            "grounded_span_support": 0.90,
            "deferral_accuracy": 0.95,
            "safety_accuracy": 0.95,
            "retrieval_p95_ms": 450,
        }
    )
    quality = next(row for row in matrix["checks"] if row["id"] == "rag.quality")
    quality["targets"]["recall_at_5"]["value"] = 0.81
    assert thresholds_from_matrix(matrix)["recall_at_5"] == 0.81
    assert (
        "real_claude_groundedness" not in THRESHOLDS
    )  # fixture report cannot pass semantic review


def test_negative_query_retrieval_failure_is_separate_from_answer_deferral():
    from roma.eval.rag_benchmark import summarize

    base = {
        "relevant": [],
        "expected_deferral": True,
        "fallback": True,
        "span_supported": False,
        "safe": True,
        "retrieval_ms": 10,
        "route_ok": True,
        "safety_case": False,
    }
    result = summarize(
        [
            base | {"case_id": "irrelevant-evidence", "retrieved": ["wrong"]},
            base | {"case_id": "abstained", "retrieved": []},
            base | {"case_id": "bypassed", "retrieved": [], "retrieval_ms": None},
            base
            | {
                "case_id": "answerable",
                "expected_deferral": False,
                "relevant": ["right"],
                "retrieved": ["right"],
            },
        ]
    )
    assert result["deferral_accuracy"] == 1
    assert result["retrieval_abstention_accuracy"] == 2 / 3
    assert result["negative_query_retrieval_failure_rate"] == 1 / 3
    assert result["failed_negative_query_ids"] == ["irrelevant-evidence"]
    assert result["counts"]["failed_negative_queries"] == 1
    assert result["counts"]["expected_deferrals"] == 3
    assert result["counts"]["search_executed_negative_queries"] == 2
    assert result["counts"]["search_executed_failed_negative_queries"] == 1
    assert result["search_executed_negative_query_failure_rate"] == 0.5
    assert result["search_executed_failed_negative_query_ids"] == ["irrelevant-evidence"]


def test_fixture_oracle_defers_even_when_negative_query_retrieves_irrelevant_evidence():
    from roma.eval.rag_benchmark import (
        FixtureRepository,
        evaluate_cases,
        load_golden,
        report_for,
    )
    from roma.services.knowledge_retrieval import KnowledgeSearchService

    golden = load_golden(Path(__file__).resolve().parents[2] / "benchmarks/rag/golden-v1.json")
    case = next(
        row for row in golden["cases"] if row["case_id"] == "heldout-unknown-schedule-hi-en"
    )
    assert case["quote"] is None
    repo = FixtureRepository(golden)
    # Deliberately admit a schedule chunk for the unsupported diving question.
    repo.provider.texts[case["query"]] = next(
        index
        for index, source in enumerate(golden["sources"])
        if source["source_key"] == "rag-golden/batches"
    )
    search = KnowledgeSearchService(
        repo,
        repo.provider,
        audit_repository=repo,
        now_fn=lambda: repo.now,
    )
    selected = golden | {"cases": [case]}
    rows = asyncio.run(evaluate_cases(selected, search, repo.stable_keys))
    assert rows[0]["retrieved"] and rows[0]["fallback"]
    report = report_for(selected, rows, repo.provider.identity, mode="mock", threshold=0.8)
    assert report["summary"]["deferral_accuracy"] == 1
    assert report["summary"]["retrieval_abstention_accuracy"] == 0
    assert report["summary"]["negative_query_retrieval_failure_rate"] == 1
    assert report["summary"]["counts"]["failed_negative_queries"] == 1
    assert report["summary"]["failed_negative_query_ids"] == [case["case_id"]]
    assert report["language_summaries"]["hi-en"]["failed_negative_query_ids"] == [
        case["case_id"]
    ]
    assert report["summary"]["search_executed_negative_query_failure_rate"] == 1
    assert report["summary"]["counts"]["search_executed_negative_queries"] == 1
    assert report["summary"]["search_executed_failed_negative_query_ids"] == [case["case_id"]]
    assert case["case_id"] in report["failures"]
    assert report["retrieval_diagnostic_failures"] == [case["case_id"]]
    assert report["mechanical_checks"]["deferral_accuracy"] is True
    assert report["quality_gate_passed"] is False
    assert "retrieval_abstention_accuracy" not in report["thresholds"]
    assert "negative_query_retrieval_failure_rate" not in report["mechanical_checks"]
