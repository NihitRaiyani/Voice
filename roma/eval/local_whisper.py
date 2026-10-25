"""Verify the one approved Whisper base artifact set before offline lab inference."""

import hashlib
import json
import os
import platform
from importlib.metadata import version
from pathlib import Path

from roma.eval.asr_benchmark import digest

SPEC = Path(__file__).resolve().parents[2] / "benchmarks/asr/whisper-base-artifacts.json"


def local_base_details(model_dir: Path) -> dict:
    if os.environ.get("HF_HUB_OFFLINE") != "1":
        raise ValueError("Start Python with HF_HUB_OFFLINE=1 for the local benchmark")
    spec = json.loads(SPEC.read_text())
    if model_dir.is_symlink() or not model_dir.is_dir():
        raise ValueError("Local base model directory is unavailable")
    # An unexpected preprocessor file could change the verified feature extraction.
    if {p.name for p in model_dir.iterdir() if p.is_file()} != set(spec["files"]):
        raise ValueError("Local base artifact set differs from the approved snapshot")
    for name, identity in spec["files"].items():
        path = model_dir / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size != identity["bytes"]:
            raise ValueError("Local base artifact size/type differs")
        checksum = hashlib.sha256()
        with path.open("rb") as stream:
            while block := stream.read(1024 * 1024):
                checksum.update(block)
        if checksum.hexdigest() != identity["sha256"]:
            raise ValueError("Local base artifact checksum differs")
    return {
        "model_name": spec["repository"],
        "model_revision": spec["revision"],
        "model_sha256": spec["files"]["model.bin"]["sha256"],
        "artifact_manifest_sha256": digest(spec),
        "artifact_source": "https://huggingface.co/"
        + spec["repository"]
        + "/tree/"
        + spec["revision"],
        "device": "cpu",
        "compute_type": "int8",
        "cpu_threads": 2,
        "num_workers": 1,
        "beam_size": 1,
        "condition_on_previous_text": False,
        "vad_filter": False,
        "timeout_secs": 60,
        "runtime_versions": {
            name: version(name) for name in ("faster-whisper", "ctranslate2", "av")
        },
        "machine": platform.machine(),
        "network_calls": False,
        "setup": "Only approved multilingual Whisper base weights provisioned separately; inference is cache-only",
    }


def local_mlx_details(model_dir: Path, *, booking_vocabulary: bool = False) -> dict:
    from roma.providers.ai.whisper_mlx import verify_mlx_artifacts
    from roma.providers.ai.whisper_vocabulary import (
        ROUTER_VERSION,
        prompt_details,
        sample_length,
    )

    vocabulary = prompt_details(booking_vocabulary)
    spec = verify_mlx_artifacts(model_dir)
    return {
        "model_name": spec["repository"], "model_revision": spec["revision"],
        "model_sha256": spec["files"]["weights.safetensors"]["sha256"],
        "artifact_manifest_sha256": digest(spec),
        "artifact_source": "https://huggingface.co/" + spec["repository"] + "/tree/" + spec["revision"],
        "device": "metal", "compute_type": "int4", "temperature": 0,
        "sample_len": sample_length(booking_vocabulary), "without_timestamps": True,
        "condition_on_previous_text": False, "cache_limit_bytes": 268435456,
        "router_version": ROUTER_VERSION if booking_vocabulary else "whisper-turbo-structural-v3",
        **({"prompt_policy": vocabulary} if booking_vocabulary else {}),
        "mixed_batch": {"max_chunks": 2, "silence_ms": 384, "min_gap_ms": 256, "min_chunk_ms": 1000, "english_probability": 0.8, "preserve_all_pcm": True, "compact_completed_rows": True, "eligibility": "audio-gap", "min_turn_ms": 2000}, "timeout_secs": 60,
        "runtime_versions": {name: version(name) for name in ("mlx", "mlx-metal", "mlx-whisper")},
        "machine": platform.machine(), "network_calls": False,
        "setup": "Approved Turbo 4-bit; no-gap decoding unchanged; qualifying paused turns use at most two full-coverage speech chunks in one compacted model batch",
    }
