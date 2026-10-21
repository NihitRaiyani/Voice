"""Reproducible public ASR evaluation, independent of network and persistence.

Only declared, hash-checked FLEURS cache inputs are accepted. Stitched bilingual
read speech is a synthetic stress test, never native conversational code-mix.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import platform
import random
import re
import wave
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from roma.domain.audio.g711 import decode, encode
from roma.domain.audio.pcm import pack, unpack
from roma.domain.audio.resampling import PCMResampler
from roma.eval.hosted_asr import normalized
from roma.providers.ai.contracts import AudioFormat, STTProvider, STTRequest
from roma.providers.ai.hf_asr import HFASRQuotaExceeded

LANGUAGES = {"gu": "gu_in", "hi": "hi_in", "en": "en_us"}
VARIANTS = ("clean", "noise", "telephone", "noise_telephone")
MANIFEST_SCHEMA = "roma-asr-manifest-v1"
REPORT_SCHEMA = "roma-asr-benchmark-v1"
NORMALIZATION = "nfc-casefold-unicode-lmn-decimal-words-char-no-spaces-v1"
MAX_AUDIO_BYTES = 30 * 32000
MAX_CASES = 400


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
    ).hexdigest()


def edit_counts(reference: Sequence, hypothesis: Sequence) -> dict[str, int]:
    """Minimum edit alignment; ties choose substitution, then deletion, insertion."""
    previous = [(i, 0, 0, i) for i in range(len(hypothesis) + 1)]
    for i, expected in enumerate(reference, 1):
        current = [(i, 0, i, 0)]
        for j, actual in enumerate(hypothesis, 1):
            if expected == actual:
                current.append(previous[j - 1])
                continue
            cost, subs, dels, ins = previous[j - 1]
            substitution = (cost + 1, subs + 1, dels, ins)
            cost, subs, dels, ins = previous[j]
            deletion = (cost + 1, subs, dels + 1, ins)
            cost, subs, dels, ins = current[j - 1]
            insertion = (cost + 1, subs, dels, ins + 1)
            current.append(min((substitution, deletion, insertion), key=lambda item: item[0]))
        previous = current
    edits, subs, dels, ins = previous[-1]
    return {"edits": edits, "substitutions": subs, "deletions": dels, "insertions": ins}


def score(reference: str, hypothesis: str) -> dict[str, int | float | None]:
    ref, hyp = normalized(reference), normalized(hypothesis)
    words, chars = ref.split(), ref.replace(" ", "")
    word = edit_counts(words, hyp.split())
    char = edit_counts(chars, hyp.replace(" ", ""))
    return {
        "reference_words": len(words),
        "word_edits": word["edits"],
        "substitutions": word["substitutions"],
        "deletions": word["deletions"],
        "insertions": word["insertions"],
        "wer": word["edits"] / len(words) if words else None if hyp else 0.0,
        "reference_chars": len(chars),
        "char_edits": char["edits"],
        "char_substitutions": char["substitutions"],
        "char_deletions": char["deletions"],
        "char_insertions": char["insertions"],
        "cer": char["edits"] / len(chars) if chars else None if hyp else 0.0,
    }


def read_wav(path: Path) -> bytes:
    if path.is_symlink() or path.stat().st_size > MAX_AUDIO_BYTES + 65536:
        raise ValueError("Public WAV must be a bounded regular cache file")
    with wave.open(str(path), "rb") as wav:
        if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getcomptype()) != (
            1,
            2,
            16000,
            "NONE",
        ):
            raise ValueError("Expected mono 16 kHz PCM16 public WAV")
        frames = wav.getnframes()
        if not 0 < frames <= MAX_AUDIO_BYTES // 2:
            raise ValueError("Public audio must contain at most thirty seconds")
        pcm = wav.readframes(frames + 1)
        if len(pcm) != frames * 2:
            raise ValueError("Public WAV is truncated")
        return pcm


def read_json(path: Path) -> dict:
    if path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError("Benchmark JSON exceeds the bound")
    result = json.loads(path.read_text())
    if not isinstance(result, dict):
        raise ValueError("Expected benchmark JSON object")
    return result


def write_json(path: Path, value: dict) -> None:
    """Checkpoint atomically, including after each completed request."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    )
    temporary.replace(path)


def _source(path: Path, audio_dir: Path) -> dict:
    metadata = read_json(path)
    language = next(
        (lang for lang in LANGUAGES if metadata.get("config") == LANGUAGES[lang]), None
    )
    row = metadata.get("row")
    wav = path.with_suffix(".wav")
    pcm = read_wav(wav)
    if (
        language is None
        or metadata.get("dataset") != "google/fleurs"
        or metadata.get("split") != "validation"
        or metadata.get("license") != "CC-BY-4.0"
        or type(row) is not int
        or not 0 <= row < 10000
        or path.parent.resolve() != audio_dir.resolve()
        or path.is_symlink()
        or hashlib.sha256(wav.read_bytes()).hexdigest() != metadata.get("sha256")
    ):
        raise ValueError("Public benchmark cache provenance/identity does not match")
    reference = metadata.get("reference")
    if not isinstance(reference, str):
        raise ValueError("Public source requires a reference transcript")
    normalized(reference)
    return {
        "source_id": f"{language}-{row:04d}",
        "language": language,
        "wav_file": wav.name,
        **{
            key: metadata[key]
            for key in ("dataset", "config", "split", "row", "license", "sha256", "reference")
        },
        "audio_seconds": len(pcm) / 32000,
    }


def build_manifest(
    audio_dir: Path,
    *,
    include_stitched_code_mix: bool = False,
    variants: tuple[str, ...] = VARIANTS,
    seed: int = 18,
    snr_db: float = 15.0,
) -> dict:
    if (
        not variants
        or len(set(variants)) != len(variants)
        or any(variant not in VARIANTS for variant in variants)
        or type(seed) is not int
        or not 0 <= seed <= 2**32 - 1
        or not math.isfinite(snr_db)
        or not 0 <= snr_db <= 40
    ):
        raise ValueError("Invalid bounded benchmark perturbation settings")
    paths = sorted(
        path
        for path in audio_dir.glob("*.json")
        if re.fullmatch(r"(?:gu|hi|en)(?:-\d{4})?", path.stem)
    )
    if not paths or len(paths) > 60:
        raise ValueError("Expected one to sixty public cache metadata files")
    sources = [_source(path, audio_dir) for path in paths]
    ids = [source["source_id"] for source in sources]
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate public source identity")
    if set(source["language"] for source in sources) != set(LANGUAGES):
        raise ValueError("Gujarati, Hindi and English public inputs are required")
    groups = [
        (source["language"], "public_read_speech", [source["source_id"]]) for source in sources
    ]
    if include_stitched_code_mix:
        english = [source for source in sources if source["language"] == "en"]
        for language in ("gu", "hi"):
            for index, source in enumerate(s for s in sources if s["language"] == language):
                partner = english[index % len(english)]
                if source["audio_seconds"] + partner["audio_seconds"] + 0.25 > 30:
                    raise ValueError(
                        "Stitched public pair exceeds thirty seconds; choose shorter sources"
                    )
                groups.append(
                    (
                        f"{language}-en",
                        "stitched_public_code_mix",
                        [source["source_id"], partner["source_id"]],
                    )
                )
    cases = []
    for language, corpus_kind, source_ids in groups:
        for variant in variants:
            cases.append(
                {
                    "case_id": "+".join(source_ids) + ":" + variant,
                    "source_ids": source_ids,
                    "language": language,
                    "corpus_kind": corpus_kind,
                    "variant": variant,
                }
            )
    if len(cases) > MAX_CASES:
        raise ValueError("Benchmark manifest exceeds the case bound")
    return {
        "schema": MANIFEST_SCHEMA,
        "data_classification": "public_benchmark",
        "normalization": NORMALIZATION,
        "derivation": {
            "seed": seed,
            "snr_db": snr_db,
            "stitch_gap_ms": 250,
            "telephone": "fir-16k-8k-g711-mulaw-8k-16k-v1",
            "noise": "seeded-uniform-rms-snr-v1",
        },
        "sources": sources,
        "cases": cases,
        "native_code_mix_included": False,
    }


def validate_manifest(manifest: dict, audio_dir: Path) -> dict[str, bytes]:
    if (
        manifest.get("schema") != MANIFEST_SCHEMA
        or manifest.get("data_classification") != "public_benchmark"
        or manifest.get("normalization") != NORMALIZATION
        or not isinstance(manifest.get("cases"), list)
        or not 0 < len(manifest["cases"]) <= MAX_CASES
    ):
        raise ValueError("Invalid public benchmark manifest")
    # Rebuild from the checked cache to prevent edited paths, references, or derivations.
    rebuilt = build_manifest(
        audio_dir,
        include_stitched_code_mix=any(
            case["corpus_kind"] == "stitched_public_code_mix" for case in manifest["cases"]
        ),
        variants=tuple(dict.fromkeys(case["variant"] for case in manifest["cases"])),
        seed=manifest["derivation"]["seed"],
        snr_db=manifest["derivation"]["snr_db"],
    )
    if rebuilt != manifest:
        raise ValueError("Manifest differs from its checked public cache")
    return {
        source["source_id"]: read_wav(audio_dir / source["wav_file"])
        for source in manifest["sources"]
    }


def perturb(pcm: bytes, variant: str, *, seed: int = 18, snr_db: float = 15) -> bytes:
    if variant not in VARIANTS or not pcm or len(pcm) > MAX_AUDIO_BYTES or len(pcm) % 2:
        raise ValueError("Invalid bounded benchmark audio/variant")
    if type(seed) is not int or not math.isfinite(snr_db) or not 0 <= snr_db <= 40:
        raise ValueError("Invalid noise configuration")
    if variant in {"noise", "noise_telephone"}:
        samples = unpack(pcm)
        rms = math.sqrt(sum(sample * sample for sample in samples) / len(samples))
        rng = random.Random(seed)
        noise = [rng.uniform(-1, 1) for _ in samples]
        noise_rms = math.sqrt(sum(value * value for value in noise) / len(noise))
        gain = rms / (10 ** (snr_db / 20)) / noise_rms
        pcm = pack(
            [
                max(-32768, min(32767, round(sample + value * gain)))
                for sample, value in zip(samples, noise, strict=True)
            ]
        )
    if variant in {"telephone", "noise_telephone"}:
        original_bytes = len(pcm)
        down, up = PCMResampler(16000, 8000), PCMResampler(8000, 16000)
        narrow = down.feed(pcm) + down.finish()
        result = up.feed(decode(encode(narrow))) + up.finish()
        # Compensate the two FIR delays (31 + 31 input-rate samples), retaining duration.
        pcm = result[124 : 124 + len(pcm)]
        if len(pcm) != original_bytes:
            raise ValueError("Telephone output is unaligned")
    return pcm


def case_audio(case: dict, manifest: dict, sources: dict[str, bytes]) -> bytes:
    pcm = b"\0" * 8000
    pcm = pcm.join(sources[source_id] for source_id in case["source_ids"])
    settings = manifest["derivation"]
    # Each case's noise remains stable even when cases are reordered or resumed.
    seed = settings["seed"] ^ int(hashlib.sha256(case["case_id"].encode()).hexdigest()[:8], 16)
    return perturb(pcm, case["variant"], seed=seed, snr_db=settings["snr_db"])


def summaries(cases: list[dict]) -> list[dict]:
    groups: dict[tuple, list] = {}
    for case in cases:
        groups.setdefault((case["language"], case["variant"], case["corpus_kind"]), []).append(
            case
        )
    result = []
    for (language, variant, kind), rows in sorted(groups.items()):
        good = [row for row in rows if row["status"] == "ok"]
        totals = {
            key: sum(row["metrics"][key] for row in good)
            for key in (
                "reference_words",
                "word_edits",
                "substitutions",
                "deletions",
                "insertions",
                "reference_chars",
                "char_edits",
            )
        }
        seconds = sum(row["audio_seconds"] for row in good)
        request_ms = sum(row["request_ms"] for row in good)
        result.append(
            {
                "language": language,
                "variant": variant,
                "corpus_kind": kind,
                "attempted": len(rows),
                "succeeded": len(good),
                "failed": len(rows) - len(good),
                **totals,
                "wer": totals["word_edits"] / totals["reference_words"]
                if totals["reference_words"]
                else None,
                "cer": totals["char_edits"] / totals["reference_chars"]
                if totals["reference_chars"]
                else None,
                "audio_seconds": seconds,
                "request_ms": request_ms,
                "request_rtf": request_ms / 1000 / seconds if seconds else None,
                "inference_rtf": None,
            }
        )
    return result


def failure_samples(report: dict, *, min_wer: float = 0.25) -> list[dict]:
    if report.get("schema") != REPORT_SCHEMA or not math.isfinite(min_wer) or min_wer < 0:
        raise ValueError("Invalid failure report/threshold")
    return [
        row
        for row in report["cases"]
        if row["status"] != "ok"
        or row["metrics"]["wer"] is None
        or row["metrics"]["wer"] >= min_wer
    ]


async def run_benchmark(
    manifest: dict,
    audio_dir: Path,
    provider: STTProvider,
    *,
    provider_name: str,
    report_path: Path,
    resume: bool = False,
    retry_errors: bool = False,
    case_ids: set[str] | None = None,
    clock: Callable[[], float] | None = None,
    languages: set[str] | None = None,
    provider_details: dict | None = None,
) -> dict:
    """Sequential provider calls; injected providers remain fully offline in tests.

    The CLI owns network authorization. No provider construction or model loading
    occurs here. Cancellation leaves the last atomic checkpoint resumable.
    """
    sources = validate_manifest(manifest, audio_dir)
    identity = digest(manifest)
    if not re.fullmatch(r"[a-z0-9_-]{1,64}", provider_name):
        raise ValueError("Invalid provider identity")
    if case_ids is not None and not case_ids <= {case["case_id"] for case in manifest["cases"]}:
        raise ValueError("Unknown replay case")
    if languages is not None and (
        not languages or not languages <= {*LANGUAGES, "gu-en", "hi-en"}
    ):
        raise ValueError("Unknown benchmark language selection")
    selected = [
        case for case in manifest["cases"] if languages is None or case["language"] in languages
    ]
    if not selected:
        raise ValueError("No cases match the language selection")
    selection = sorted({case["language"] for case in selected})
    report: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "manifest_schema": MANIFEST_SCHEMA,
        "dataset": "google/fleurs",
        "dataset_revision": None,
        "derivation": manifest["derivation"],
        "source_count": len(manifest["sources"]),
        "environment": {
            "python_version": platform.python_version(),
            "audio_implementation": "python-reference-v1",
        },
        "manifest_sha256": identity,
        "recorded_at": datetime.now(UTC).isoformat(),
        "provider": provider_name,
        "normalization": NORMALIZATION,
        "cases": [],
        "summaries": [],
        "languages": selection,
        "provider_details": provider_details or {},
        "public_samples_only": True,
        "new_model_weights_downloaded": False,
        "paid_router_used": False,
        "native_review_passed": False,
        "production_voice_approved": False,
        "native_code_mix_included": False,
        "limits": "Small public read-speech lab. Stitched code-mix is synthetic, not native conversation. Noise is uniform synthetic noise; telephone is a codec/filter simulation, not a carrier call. Request RTF includes upload, queue and inference; model compute RTF is unknown. No quality gate or production approval is implied.",
        "expected_cases": len(selected),
        "complete": False,
    }
    if provider_name in {"whisper_local", "indic_local", "single_pass_local"}:
        report["network_calls"] = False
        report["limits"] = (
            "Small public read-speech lab; stitched code-mix is synthetic. Local CPU wall RTF "
            "includes first-case model loading, feature extraction, decoding and worker overhead; "
            "inference_rtf is not isolated. Weights were provisioned before this offline run. "
            "No native review, microphone test or production approval is implied."
        )
    if case_ids is not None and not resume:
        raise ValueError("Replay requires an existing report checkpoint")
    previous: dict[str, dict] = {}
    if resume and await asyncio.to_thread(report_path.exists):
        saved = read_json(report_path)
        if (
            saved.get("schema") != REPORT_SCHEMA
            or saved.get("manifest_sha256") != identity
            or saved.get("provider") != provider_name
            or saved.get("languages") != selection
            or saved.get("provider_details") != (provider_details or {})
        ):
            raise ValueError("Resume report identity differs from the manifest/provider")
        expected = {case["case_id"]: case for case in selected}
        for row in saved["cases"]:
            case = expected.get(row["case_id"])
            if case is None or row["case_id"] in previous:
                raise ValueError("Resume report contains unknown/duplicate cases")
            reference = " ".join(
                source["reference"]
                for source_id in case["source_ids"]
                for source in manifest["sources"]
                if source["source_id"] == source_id
            )
            pcm = case_audio(case, manifest, sources)
            if (
                any(row.get(key) != value for key, value in case.items())
                or row.get("audio_sha256") != hashlib.sha256(pcm).hexdigest()
                or row.get("reference") != reference
                or row.get("audio_seconds") != len(pcm) / 32000
                or row.get("status") not in {"ok", "error"}
                or (
                    row["status"] == "ok"
                    and row.get("metrics") != score(reference, row["hypothesis"])
                )
                or (row["status"] == "error" and row.get("metrics") is not None)
            ):
                raise ValueError("Resume report case identity/score differs")
            previous[row["case_id"]] = row
        report["recorded_at"] = saved["recorded_at"]
    timer = clock or asyncio.get_running_loop().time
    references = {source["source_id"]: source["reference"] for source in manifest["sources"]}
    report["cases"] = [
        previous[case["case_id"]] for case in selected if case["case_id"] in previous
    ]
    write_json(report_path, report)
    for case in selected:
        case_id = case["case_id"]
        old = previous.get(case_id)
        if case_ids is not None and case_id not in case_ids:
            continue
        if old and not (retry_errors and old["status"] == "error") and case_ids is None:
            continue
        pcm = case_audio(case, manifest, sources)
        reference = " ".join(references[source_id] for source_id in case["source_ids"])
        row = {
            **case,
            "reference": reference,
            "audio_sha256": hashlib.sha256(pcm).hexdigest(),
            "audio_seconds": len(pcm) / 32000,
            "status": "error",
            "error_code": None,
            "metrics": None,
            "hypothesis": None,
            "inference_rtf": None,
        }
        started = timer()
        elapsed = None
        try:
            if provider_name == "indic_local" and case["language"] == "en":
                row["error_code"] = "asr-language-unsupported"
            else:
                result = await provider.transcribe(
                    STTRequest(
                        pcm,
                        AudioFormat(16000),
                        (
                            case["language"]
                            if provider_name == "single_pass_local"
                            else case["language"].split("-")[0]
                        ),
                        {"data_classification": "public_benchmark", "benchmark_case": case_id},
                    )
                )
                row.update(
                    status="ok", hypothesis=result.text, metrics=score(reference, result.text)
                )
            elapsed = max(0.0, timer() - started)
        except asyncio.CancelledError:
            raise
        except HFASRQuotaExceeded:
            row["error_code"] = "asr-hosted-quota-exhausted"
        except Exception:  # noqa: BLE001 -- retain fixed code, never remote body/token details.
            row["error_code"] = "asr-request-failed"
        if elapsed is None:
            elapsed = max(0.0, timer() - started)
        row["request_ms"] = round(elapsed * 1000, 3)
        row["request_rtf"] = row["request_ms"] / 1000 / row["audio_seconds"]
        previous[case_id] = row
        report["cases"] = [
            previous[item["case_id"]] for item in selected if item["case_id"] in previous
        ]
        report["summaries"] = summaries(report["cases"])
        report["complete"] = len(report["cases"]) == len(selected)
        write_json(report_path, report)
        if row["error_code"] == "asr-hosted-quota-exhausted":
            report["stopped_reason"] = "asr-hosted-quota-exhausted"
            break
    report["summaries"] = summaries(report["cases"])
    report["complete"] = len(report["cases"]) == len(selected)
    if report["complete"]:
        report["completed_at"] = datetime.now(UTC).isoformat()
    write_json(report_path, report)
    return report


def export_failure_samples(
    report: dict, manifest: dict, audio_dir: Path, output_dir: Path, *, min_wer: float = 0.25
) -> list[Path]:
    """Save only verified public/derived failure WAVs plus review metadata."""
    sources = validate_manifest(manifest, audio_dir)
    if report.get("manifest_sha256") != digest(manifest):
        raise ValueError("Failure report does not match the public manifest")
    cases = {case["case_id"]: case for case in manifest["cases"]}
    exported = []
    for row in failure_samples(report, min_wer=min_wer):
        case = cases[row["case_id"]]
        pcm = case_audio(case, manifest, sources)
        if hashlib.sha256(pcm).hexdigest() != row["audio_sha256"]:
            raise ValueError("Failure audio identity differs from the report")
        # File identity comes from the verified manifest; no report-controlled paths.
        stem = digest({"case_id": case["case_id"]})[:24]
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / (stem + ".wav")
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(pcm)
        write_json(
            path.with_suffix(".json"),
            {
                "schema": "roma-asr-failure-v1",
                "data_classification": "public_benchmark",
                "manifest_sha256": digest(manifest),
                "case": row,
            },
        )
        exported.append(path)
    return exported
