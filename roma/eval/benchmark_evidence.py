"""Convert completed public/synthetic lab reports into transcript-free SQL inputs."""

from __future__ import annotations

import hashlib
import json
import math
import re
import stat
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from roma.eval.asr_benchmark import summaries
from roma.repositories.postgres.benchmarks import (
    AggregateMetric,
    BenchmarkEvidence,
    FailureArtifact,
    ModelEvidence,
)

MAX_REPORT_BYTES = 8 * 1024 * 1024


def read_report(path: Path) -> tuple[dict, str]:
    """Bound the read itself, reject symlinks/devices and non-finite JSON."""
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError("Benchmark report must be a regular file")
    with path.open("rb") as stream:
        raw = stream.read(MAX_REPORT_BYTES + 1)
    if len(raw) > MAX_REPORT_BYTES:
        raise ValueError("Benchmark report exceeds the size bound")

    def invalid_constant(_value):
        raise ValueError("Non-finite report JSON")

    report = json.loads(raw, parse_constant=invalid_constant)
    if not isinstance(report, dict):
        raise ValueError("Benchmark report must be an object")
    return report, hashlib.sha256(raw).hexdigest()


def _timestamp(report: dict, key: str) -> datetime:
    value = datetime.fromisoformat(report[key])
    if value.utcoffset() is None:
        raise ValueError("Benchmark report requires timezone-aware timestamps")
    return value


def _checksum(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("Benchmark report requires a SHA256 identity")
    return value


def _finite(value: object, *, positive: bool = False) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise ValueError("Invalid finite benchmark measurement")
    if value < 0 or (positive and value == 0):
        raise ValueError("Invalid nonnegative benchmark measurement")
    return float(value)


def _endpoint(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Missing benchmark endpoint provenance")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Unsafe benchmark endpoint provenance")
    return value


def _asr(
    report: dict,
) -> tuple[list[ModelEvidence], list[AggregateMetric], dict, str, str, str]:
    if report.get("complete") is not True or report.get("public_samples_only") is not True:
        raise ValueError("Only completed public ASR benchmark reports can be saved")
    provider = report["provider"]
    if provider not in {"hf_indic", "hf_whisper_space", "hf_public_lab", "whisper_local"}:
        raise ValueError("Unsupported ASR benchmark provider")
    cases = report["cases"]
    if (
        not isinstance(cases, list)
        or not 0 < len(cases) <= 400
        or len(cases) != report["expected_cases"]
    ):
        raise ValueError("Incomplete ASR benchmark cases")
    ids = set()
    for case in cases:
        if case["case_id"] in ids or case["status"] not in {"ok", "error"}:
            raise ValueError("Invalid ASR case identity/status")
        ids.add(case["case_id"])
        if (
            case["language"] not in {"gu", "hi", "en", "gu-en", "hi-en"}
            or case["variant"] not in {"clean", "noise", "telephone", "noise_telephone"}
            or case["corpus_kind"] not in {"public_read_speech", "stitched_public_code_mix"}
        ):
            raise ValueError("Invalid ASR summary scope")
        _checksum(case["audio_sha256"])
        _finite(case["audio_seconds"], positive=True)
        _finite(case["request_ms"])
        if case["status"] == "ok":
            for key in (
                "reference_words",
                "word_edits",
                "substitutions",
                "deletions",
                "insertions",
                "reference_chars",
                "char_edits",
            ):
                if type(case["metrics"][key]) is not int or case["metrics"][key] < 0:
                    raise ValueError("Invalid ASR edit counts")
    checked_summaries = summaries(cases)
    if checked_summaries != report["summaries"]:
        raise ValueError("ASR aggregate measurements differ from case evidence")
    details = report["provider_details"]
    whisper_api_name = details.get("whisper_api_name")
    if whisper_api_name is not None and whisper_api_name not in {"predict", "transcribe"}:
        raise ValueError("Unknown benchmark Whisper API name")
    authenticated = details.get("whisper_authenticated")
    if authenticated is not None and type(authenticated) is not bool:
        raise ValueError("Invalid Whisper authentication provenance")
    local_model = None
    if provider == "whisper_local":
        if (
            report.get("network_calls") is not False
            or details.get("network_calls") is not False
            or details.get("model_name") != "Systran/faster-whisper-base"
            or not re.fullmatch(r"[0-9a-f]{40}", details.get("model_revision", ""))
            or details.get("device") != "cpu"
            or details.get("compute_type") != "int8"
        ):
            raise ValueError("Invalid local base benchmark provenance")
        local_model = ModelEvidence(
            "faster-whisper",
            details["model_name"],
            details["model_revision"],
            artifact_uri=_endpoint(details["artifact_source"]),
            checksum_sha256=_checksum(details["model_sha256"]),
            license_name="MIT",
        )
        _checksum(details["artifact_manifest_sha256"])
    model_by_language = {}
    models = {}
    for language in {case["language"] for case in cases}:
        if local_model is not None:
            models[local_model.key] = local_model
            model_by_language[language] = local_model
            continue
        whisper = provider == "hf_whisper_space" or (
            provider == "hf_public_lab" and language == "en"
        )
        if provider == "hf_indic" and language == "en":
            raise ValueError("Indic benchmark cannot claim English routing")
        endpoint = _endpoint(details["whisper_endpoint" if whisper else "indic_endpoint"])
        decoder = "whisper-space" if whisper else details["decoder"]
        if decoder not in {"whisper-space", "ctc", "rnnt"}:
            raise ValueError("Unknown benchmark decoder")
        # Public Spaces do not expose a trustworthy immutable weight revision.
        protocol_identity = str(decoder)
        if whisper and whisper_api_name is not None:
            protocol_identity += ":" + whisper_api_name
        version = (
            "unpinned-hosted-" + hashlib.sha256(protocol_identity.encode()).hexdigest()[:16]
        )
        model = ModelEvidence(
            "huggingface-space",
            urlsplit(endpoint).netloc + urlsplit(endpoint).path.rstrip("/"),
            version,
            artifact_uri=endpoint,
        )
        models[model.key] = model
        model_by_language[language] = model
    metrics = []
    for group in checked_summaries:
        kind = "read" if group["corpus_kind"] == "public_read_speech" else "stitched"
        scope = group["variant"] + "." + kind
        metadata = {
            key: group[key]
            for key in (
                "variant",
                "corpus_kind",
                "attempted",
                "succeeded",
                "failed",
                "reference_words",
                "reference_chars",
                "word_edits",
                "char_edits",
            )
        }
        metadata["model_revision_known"] = False
        model_key = model_by_language[group["language"]].key
        for name, value, unit, count in (
            ("error_rate", group["failed"] / group["attempted"], "ratio", group["attempted"]),
            ("wer", group["wer"], "ratio", group["succeeded"]),
            ("cer", group["cer"], "ratio", group["succeeded"]),
            ("request_rtf", group["request_rtf"], "ratio", group["succeeded"]),
            ("request_ms", group["request_ms"], "ms", group["succeeded"]),
            ("audio_seconds", group["audio_seconds"], "seconds", group["succeeded"]),
        ):
            if value is not None and count:
                _finite(value)
                metrics.append(
                    AggregateMetric(
                        model_key,
                        group["language"],
                        name + "." + scope,
                        value,
                        unit,
                        count,
                        metadata,
                    )
                )
    config = {
        "provider": provider,
        "provider_details": {
            key: details[key]
            for key in (
                (
                    "model_name",
                    "model_revision",
                    "model_sha256",
                    "artifact_manifest_sha256",
                    "artifact_source",
                    "device",
                    "compute_type",
                    "cpu_threads",
                    "num_workers",
                    "beam_size",
                    "condition_on_previous_text",
                    "vad_filter",
                    "timeout_secs",
                    "runtime_versions",
                    "machine",
                    "network_calls",
                    "setup",
                )
                if local_model is not None
                else ("indic_endpoint", "whisper_endpoint", "decoder", "timeout_secs")
            )
        },
        "normalization": report["normalization"],
        "public_samples_only": True,
        "native_review_passed": False,
        "production_voice_approved": False,
        "model_revision_known": local_model is not None,
        "dataset_revision": report.get("dataset_revision"),
        "derivation": {
            key: report.get("derivation", {})[key]
            for key in ("seed", "snr_db", "stitch_gap_ms", "telephone", "noise")
            if key in report.get("derivation", {})
        },
        "source_count": report.get("source_count"),
    }
    if whisper_api_name is not None:
        config["provider_details"]["whisper_api_name"] = whisper_api_name
    if authenticated is not None:
        config["provider_details"]["whisper_authenticated"] = authenticated
    _finite(details["timeout_secs"], positive=True)
    if local_model is None:
        for key in ("indic_endpoint", "whisper_endpoint"):
            _endpoint(config["provider_details"][key])
    status = "failed" if any(case["status"] == "error" for case in cases) else "succeeded"
    return (
        list(models.values()),
        metrics,
        config,
        "google/fleurs-public-derived",
        "roma-asr-manifest-v1",
        status,
    )


def _tts(
    report: dict,
) -> tuple[list[ModelEvidence], list[AggregateMetric], dict, str, str, str]:
    provider = report["provider"]
    if (
        provider not in {"indic_tts", "synthetic_pcm"}
        or report.get("network_calls") is not False
        or report.get("model_downloads") is not False
    ):
        raise ValueError("Unsupported offline TTS benchmark provenance")
    cases = report["cases"]
    if (
        not isinstance(cases, list)
        or not 0 < len(cases) <= 1000
        or type(report.get("expected_cases")) is not int
        or report["expected_cases"] != len(cases)
    ):
        raise ValueError("Incomplete TTS benchmark cases")
    if report.get("synthetic_only") is not (provider == "synthetic_pcm"):
        raise ValueError("TTS synthetic provenance does not match provider")
    models = {}
    groups = defaultdict(list)
    attempts = defaultdict(list)
    identities = set()
    checksum = _checksum(report["model_manifest_sha256"])
    failed = 0
    for case in cases:
        status = case.get("status", "ok")
        if (
            case["language"] not in {"gu", "hi", "en"}
            or case["length"] not in {"short", "medium", "long"}
            or type(case["repetition"]) is not int
            or case["repetition"] not in {0, 1}
            or status not in {"ok", "error"}
        ):
            raise ValueError("Invalid TTS summary scope")
        identity = case["language"], case["length"], case["repetition"]
        if identity in identities:
            raise ValueError("Duplicate TTS benchmark case identity")
        identities.add(identity)
        if case.get("model_identity_resolved", True) is not True:
            raise ValueError("Cannot save TTS measurements with unresolved model provenance")
        version = case["model_version"]
        model = ModelEvidence("local", provider, version, "tts", checksum_sha256=checksum)
        models[model.key] = model
        attempts[(model.key, case["language"], case["length"])].append(case)
        if status == "error":
            if case["cache_status"] != "error" or not re.fullmatch(
                r"[A-Za-z][A-Za-z0-9_-]{0,63}", case["error_code"]
            ):
                raise ValueError("Invalid sanitized TTS failure evidence")
            _finite(case["request_elapsed_ms"])
            failed += 1
            continue
        if case["cache_status"] not in {"hit", "miss", "coalesced"}:
            raise ValueError("Invalid successful TTS cache status")
        for key in ("synthesis_ms", "elapsed_ms", "rtf", "audio_ms"):
            _finite(case[key], positive=key == "audio_ms")
        if case["audit_ok"] is not True:
            raise ValueError("TTS report contains an audio contract failure")
        groups[(model.key, case["language"], case["length"], case["cache_status"])].append(case)
    languages = {case["language"] for case in cases}
    expected = {
        (language, length, repetition)
        for language in languages
        for length in ("short", "medium", "long")
        for repetition in (0, 1)
    }
    if (
        identities != expected
        or type(report.get("errors")) is not int
        or report["errors"] != failed
    ):
        raise ValueError("TTS benchmark case grid or error count does not match evidence")
    metrics = []
    for (model_key, language, length), rows in attempts.items():
        errors = [row for row in rows if row.get("status", "ok") == "error"]
        metadata = {
            "length": length,
            "attempted": len(rows),
            "failed": len(errors),
            "error_codes": sorted({row["error_code"] for row in errors}),
        }
        metrics.append(
            AggregateMetric(
                model_key,
                language,
                "error_rate." + length,
                len(errors) / len(rows),
                "ratio",
                len(rows),
                metadata,
            )
        )
    for (model_key, language, length, cache_status), rows in groups.items():
        count = len(rows)
        metadata = {
            "length": length,
            "cache_status": cache_status,
            "synthetic_only": report["synthetic_only"],
            "native_review_passed": False,
        }
        for name, value, unit in (
            ("mean_synthesis_ms", sum(row["synthesis_ms"] for row in rows) / count, "ms"),
            ("mean_elapsed_ms", sum(row["elapsed_ms"] for row in rows) / count, "ms"),
            (
                "synthesis_rtf",
                sum(row["synthesis_ms"] for row in rows) / sum(row["audio_ms"] for row in rows),
                "ratio",
            ),
            ("audio_ms", sum(row["audio_ms"] for row in rows), "ms"),
        ):
            metrics.append(
                AggregateMetric(
                    model_key,
                    language,
                    name + "." + length + "." + cache_status,
                    value,
                    unit,
                    count,
                    metadata,
                )
            )
    config = {
        "provider": provider,
        "synthetic_only": report["synthetic_only"],
        "model_downloads": False,
        "network_calls": False,
        "native_review_passed": False,
        "cache": report["cache"],
        "model_manifest_sha256": checksum,
    }
    return (
        list(models.values()),
        metrics,
        config,
        "roma-tts-fixed-prompts",
        report["dataset_version"],
        "failed" if failed else "succeeded",
    )


def evidence_from_report(
    report: dict, *, run_key: str, report_path: Path, report_sha256: str
) -> BenchmarkEvidence:
    """Allowlisted aggregates only; raw text/audio never enters SQL inputs."""
    schema = report.get("schema")
    if schema == "roma-asr-benchmark-v1":
        models, metrics, config, dataset, version, status = _asr(report)
        dataset_hash = _checksum(report["manifest_sha256"])
    elif schema == "section19-tts-v1":
        models, metrics, config, dataset, version, status = _tts(report)
        dataset_hash = _checksum(report["dataset_sha256"])
    else:
        raise ValueError("Unsupported benchmark report schema")
    config.update(
        report_schema=schema,
        report_sha256=_checksum(report_sha256),
        report_path=str(report_path.resolve()),
    )
    return BenchmarkEvidence(
        run_key=run_key,
        dataset_name=dataset,
        dataset_version=version,
        dataset_sha256=dataset_hash,
        status=status,
        sample_count=len(report["cases"]),
        started_at=_timestamp(report, "recorded_at"),
        ended_at=_timestamp(report, "completed_at"),
        models=tuple(models),
        metrics=tuple(metrics),
        config=config,
        environment={
            "benchmark_environment_recorded": bool(report.get("environment")),
            **{
                key: report.get("environment", {})[key]
                for key in ("python_version", "audio_implementation")
                if key in report.get("environment", {})
            },
        },
        failure_artifacts=(FailureArtifact(str(report_path.resolve()), report_sha256),),
    )
