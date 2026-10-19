"""Report persistence is entirely offline until the CLI's explicit SQL opt-in."""

import asyncio
import hashlib
import importlib.util
import json
from argparse import Namespace
from pathlib import Path

import pytest
from roma.eval.asr_benchmark import summaries
from roma.eval.benchmark_evidence import evidence_from_report, read_report
from roma.repositories.postgres.benchmarks import _prepare


def asr_report():
    cases = []
    for index, language in enumerate(("gu", "en")):
        cases.append(
            {
                "case_id": str(index),
                "language": language,
                "variant": "clean",
                "corpus_kind": "public_read_speech",
                "status": "ok",
                "audio_sha256": "a" * 64,
                "audio_seconds": 1.0,
                "request_ms": 100.0,
                "reference": "public reference excluded from SQL",
                "hypothesis": "public hypothesis excluded from SQL",
                "metrics": {
                    "reference_words": 2,
                    "word_edits": 1,
                    "substitutions": 1,
                    "deletions": 0,
                    "insertions": 0,
                    "reference_chars": 10,
                    "char_edits": 1,
                },
            }
        )
    return {
        "schema": "roma-asr-benchmark-v1",
        "provider": "hf_public_lab",
        "public_samples_only": True,
        "complete": True,
        "expected_cases": 2,
        "cases": cases,
        "summaries": summaries(cases),
        "manifest_sha256": "b" * 64,
        "normalization": "unicode-v1",
        "recorded_at": "2026-10-19T08:00:00+00:00",
        "completed_at": "2026-10-19T08:01:00+00:00",
        "provider_details": {
            "indic_endpoint": "https://indic.example/asr",
            "whisper_endpoint": "https://whisper.example/asr",
            "decoder": "ctc",
            "timeout_secs": 45,
        },
    }


def convert(report):
    return evidence_from_report(
        report,
        run_key="offline-test",
        report_path=Path("var/roma/report.json"),
        report_sha256="c" * 64,
    )


def test_asr_routes_models_and_persists_scoped_aggregates_without_text():
    report = asr_report()
    item = convert(report)
    run, models, metrics = _prepare(item)
    assert item.dataset_sha256 == "b" * 64
    assert len(models) == 2
    assert len(metrics) == 12
    assert {row["metric"] for row in metrics} >= {
        "wer.clean.read",
        "cer.clean.read",
        "request_rtf.clean.read",
    }
    encoded = (
        json.dumps(run, default=str) + json.dumps(models) + json.dumps(metrics, default=str)
    )
    assert "public reference excluded" not in encoded
    assert "public hypothesis excluded" not in encoded
    assert all(row["version"].startswith("unpinned-hosted-") for row in models)
    assert all(row["checksum_sha256"] is None for row in models)


@pytest.mark.parametrize(
    "change",
    [
        lambda report: report.update(complete=False),
        lambda report: report.update(public_samples_only=False),
        lambda report: report["summaries"][0].update(wer=99),
        lambda report: report["cases"][0].update(audio_seconds=float("inf")),
        lambda report: report["provider_details"].update(
            indic_endpoint="https://x/asr?token=secret"
        ),
        lambda report: report.pop("completed_at"),
    ],
)
def test_incomplete_edited_or_unsafe_asr_evidence_rejected(change):
    report = asr_report()
    change(report)
    with pytest.raises((ValueError, KeyError)):
        _prepare(convert(report))


def test_asr_request_failure_retains_error_rate_and_report_artifact_identity():
    report = asr_report()
    report["cases"][0].update(status="error", metrics=None, hypothesis=None)
    report["summaries"] = summaries(report["cases"])
    item = convert(report)
    assert item.status == "failed"
    assert item.failure_artifacts[0].sha256 == "c" * 64
    assert next(row.value for row in item.metrics if row.language == "gu") == 1
    assert not any(
        row.metric.startswith("wer.") and row.language == "gu" for row in item.metrics
    )
    _prepare(item)


def tts_report():
    return {
        "schema": "section19-tts-v1",
        "provider": "synthetic_pcm",
        "synthetic_only": True,
        "network_calls": False,
        "model_downloads": False,
        "errors": 0,
        "dataset_version": "section19-prompts-v1",
        "dataset_sha256": "d" * 64,
        "model_manifest_sha256": "e" * 64,
        "recorded_at": "2026-10-19T08:00:00+00:00",
        "completed_at": "2026-10-19T08:00:01+00:00",
        "cache": {"hits": 1, "misses": 1, "stores": 1, "coalesced": 0},
        "expected_cases": 6,
        "cases": [
            {
                "status": "ok",
                "language": "gu",
                "length": length,
                "repetition": repetition,
                "cache_status": "miss" if repetition == 0 else "hit",
                "model_version": "synthetic-pcm-v1",
                "synthesis_ms": 10.0 if repetition == 0 else 0.0,
                "elapsed_ms": 12.0 if repetition == 0 else 1.0,
                "rtf": 0.01 if repetition == 0 else 0.0,
                "audio_ms": 1000,
                "audit_ok": True,
            }
            for length in ("short", "medium", "long")
            for repetition in (0, 1)
        ],
    }


def test_tts_preserves_model_manifest_and_separates_cache_metrics():
    item = convert(tts_report())
    _prepare(item)
    assert item.models[0].checksum_sha256 == "e" * 64
    assert item.dataset_sha256 == "d" * 64
    assert len(item.metrics) == 27
    assert {row.metric for row in item.metrics} >= {
        "mean_synthesis_ms.short.hit",
        "mean_synthesis_ms.short.miss",
    }
    assert item.config["synthetic_only"] is True


def test_report_reader_bounds_regular_files_and_hashes_exact_bytes(tmp_path, monkeypatch):
    path = tmp_path / "report.json"
    raw = b'{"schema":"fixture"}\n'
    path.write_bytes(raw)
    assert read_report(path) == ({"schema": "fixture"}, hashlib.sha256(raw).hexdigest())
    link = tmp_path / "link.json"
    link.symlink_to(path)
    with pytest.raises(ValueError):
        read_report(link)
    monkeypatch.setattr("roma.eval.benchmark_evidence.MAX_REPORT_BYTES", 2)
    with pytest.raises(ValueError, match="bound"):
        read_report(path)
    monkeypatch.setattr("roma.eval.benchmark_evidence.MAX_REPORT_BYTES", 100)
    path.write_text('{"value":NaN}')
    with pytest.raises(ValueError):
        read_report(path)


def test_cli_refuses_before_settings_or_database_construction():
    path = Path(__file__).resolve().parents[2] / "scripts/save_benchmark.py"
    spec = importlib.util.spec_from_file_location("save_benchmark_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with pytest.raises(ValueError, match="allow-database-write"):
        asyncio.run(module.run(Namespace(allow_database_write=False)))


def test_asr_environment_derivation_and_endpoint_provenance_are_preserved():
    report = asr_report()
    report["environment"] = {
        "python_version": "3.11.12",
        "audio_implementation": "python-reference-v1",
    }
    report["derivation"] = {
        "seed": 18,
        "snr_db": 15,
        "stitch_gap_ms": 250,
        "telephone": "fir-reference-v1",
        "noise": "seeded-uniform-v1",
    }
    item = convert(report)
    assert item.environment["python_version"] == "3.11.12"
    assert item.config["derivation"]["snr_db"] == 15
    assert item.config["provider_details"]["decoder"] == "ctc"
    _prepare(item)


def test_cli_error_does_not_echo_credentials(monkeypatch, capsys):
    path = Path(__file__).resolve().parents[2] / "scripts/save_benchmark.py"
    spec = importlib.util.spec_from_file_location("save_benchmark_error_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    async def fail(_args):
        raise RuntimeError("postgresql://user:private-secret@example/db")

    monkeypatch.setattr(module, "run", fail)
    monkeypatch.setattr(
        "sys.argv",
        [
            "save_benchmark.py",
            "--report",
            "unused.json",
            "--run-key",
            "fixture",
            "--allow-database-write",
        ],
    )
    assert module.main() == 1
    output = capsys.readouterr()
    assert "Benchmark save failed" in output.err
    assert "private-secret" not in output.err + output.out


@pytest.mark.parametrize("all_failed", [False, True])
def test_tts_failures_persist_only_observed_metrics_and_artifact_identity(all_failed):
    report = tts_report()
    for index in range(len(report["cases"]) if all_failed else 1):
        row = report["cases"][index]
        report["cases"][index] = {
            "status": "error",
            "cache_status": "error",
            "language": row["language"],
            "length": row["length"],
            "repetition": row["repetition"],
            "model_version": row["model_version"],
            "error_code": "ProviderUnavailable",
            "request_elapsed_ms": 42.0,
        }
    report["errors"] = len(report["cases"]) if all_failed else 1
    item = convert(report)
    _prepare(item)
    assert item.status == "failed"
    assert item.failure_artifacts[0].sha256 == "c" * 64
    rates = {
        row.metric: row.value for row in item.metrics if row.metric.startswith("error_rate.")
    }
    assert rates["error_rate.short"] == (1.0 if all_failed else 0.5)
    assert not any(row.metric == "mean_synthesis_ms.short.miss" for row in item.metrics)
    if all_failed:
        assert len(item.metrics) == 3
        assert set(rates.values()) == {1.0}


@pytest.mark.parametrize(
    "change",
    [
        lambda report: report.update(expected_cases=7),
        lambda report: report["cases"].pop(),
        lambda report: report["cases"].__setitem__(1, dict(report["cases"][0])),
        lambda report: report.update(errors=1),
        lambda report: report.update(synthetic_only=False),
    ],
)
def test_tts_partial_duplicate_or_inconsistent_reports_are_rejected(change):
    report = tts_report()
    change(report)
    with pytest.raises(ValueError):
        convert(report)
