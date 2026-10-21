"""Matched local-ASR comparison; errors never acquire invented accuracy scores."""

import math
from statistics import median

from roma.eval.asr_benchmark import REPORT_SCHEMA, digest, score


def _checked(report: dict, provider: str) -> dict[str, dict]:
    if (
        report.get("schema") != REPORT_SCHEMA
        or report.get("complete") is not True
        or report.get("network_calls") is not False
        or report.get("provider") != provider
        or len(report["cases"]) != report["expected_cases"]
    ):
        raise ValueError("Comparison requires complete, correctly identified offline reports")
    rows = {}
    for row in report["cases"]:
        if (
            row["case_id"] in rows
            or row["status"] not in {"ok", "error"}
            or not math.isfinite(row["request_ms"])
            or row["request_ms"] < 0
            or not math.isfinite(row["audio_seconds"])
            or row["audio_seconds"] <= 0
            or (row["status"] == "ok" and row["metrics"] != score(row["reference"], row["hypothesis"]))
            or (row["status"] == "error" and row["metrics"] is not None)
        ):
            raise ValueError("Invalid comparison case identity, score or timing")
        rows[row["case_id"]] = row
    return rows


def _measure(rows: list[dict]) -> dict:
    good = [row for row in rows if row["status"] == "ok"]
    words = sum(r["metrics"]["reference_words"] for r in good)
    chars = sum(r["metrics"]["reference_chars"] for r in good)
    seconds = sum(r["audio_seconds"] for r in good)
    return {
        "attempted": len(rows),
        "succeeded": len(good),
        "wer": sum(r["metrics"]["word_edits"] for r in good) / words if words else None,
        "cer": sum(r["metrics"]["char_edits"] for r in good) / chars if chars else None,
        "median_request_ms": median(r["request_ms"] for r in good) if good else None,
        "request_rtf": sum(r["request_ms"] for r in good) / 1000 / seconds if seconds else None,
    }


def compare_local_reports(indic: dict, whisper: dict) -> dict:
    left, right = _checked(indic, "indic_local"), _checked(whisper, "whisper_local")
    if (
        indic["manifest_sha256"] != whisper["manifest_sha256"]
        or indic["normalization"] != whisper["normalization"]
        or left.keys() != right.keys()
    ):
        raise ValueError("Comparison manifest, normalization or case identity differs")
    identity_keys = (
        "audio_sha256", "audio_seconds", "reference", "language", "variant", "corpus_kind",
    )
    for key, row in left.items():
        if any(row[field] != right[key][field] for field in identity_keys):
            raise ValueError("Comparison audio/reference identity differs")
    cold = {
        name: next((key for key, row in rows.items() if row["status"] == "ok"), None)
        for name, rows in (("indic", left), ("whisper", right))
    }
    cold_timings = {}
    for name, rows in (("indic", left), ("whisper", right)):
        cold_key = cold[name]
        cold_timings[name] = rows[cold_key]["request_ms"] if cold_key is not None else None
    shared = [key for key in left if left[key]["status"] == right[key]["status"] == "ok"]
    warm = [key for key in shared if key not in cold.values()]

    def pair(keys):
        return {
            "indic": _measure([left[key] for key in keys]),
            "whisper": _measure([right[key] for key in keys]),
        }

    return {
        "schema": "roma-local-asr-comparison-v1",
        "manifest_sha256": indic["manifest_sha256"],
        "normalization": indic["normalization"],
        "report_identities": {"indic": digest(indic), "whisper": digest(whisper)},
        "models": {
            "indic": indic["provider_details"], "whisper": whisper["provider_details"]
        },
        "expected_cases": len(left),
        "shared_success_cases": len(shared),
        "shared_success": pair(shared),
        "cold_case_ids": cold,
        "cold_request_ms": cold_timings,
        "shared_warm_cases": len(warm),
        "shared_warm": pair(warm),
        "by_language": [
            {
                "language": language,
                **pair([key for key in left if left[key]["language"] == language]),
                "shared_success_cases": sum(left[key]["language"] == language for key in shared),
                "shared_warm_cases": sum(left[key]["language"] == language for key in warm),
                "shared_warm": pair([key for key in warm if left[key]["language"] == language]),
            }
            for language in sorted({row["language"] for row in left.values()})
        ],
        "cases": [
            {
                "case_id": key, "language": row["language"], "variant": row["variant"],
                **{
                    name: {
                        "status": item["status"], "error_code": item["error_code"],
                        "wer": item["metrics"]["wer"] if item["metrics"] else None,
                        "cer": item["metrics"]["cer"] if item["metrics"] else None,
                        "request_ms": item["request_ms"] if item["status"] == "ok" else None,
                    }
                    for name, item in (("indic", row), ("whisper", right[key]))
                },
            }
            for key, row in left.items()
        ],
        "limits": (
            "Same public read-speech sources with synthetic noise/telephone/stitched variants. "
            "Weighted WER/CER on shared successes; warm latency excludes each process's first "
            "successful inference from both providers. Process-cold is not machine-cold. "
            "Different native runtimes/thread defaults; these samples do not establish p95, "
            "native code-mix quality, live voice latency or production approval."
        ),
        "native_review_passed": False,
        "production_voice_approved": False,
    }
