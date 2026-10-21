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


def local_mlx_details(model_dir: Path) -> dict:
    from roma.providers.ai.whisper_mlx import verify_mlx_artifacts

    spec = verify_mlx_artifacts(model_dir)
    return {
        "model_name": spec["repository"], "model_revision": spec["revision"],
        "model_sha256": spec["files"]["weights.safetensors"]["sha256"],
        "artifact_manifest_sha256": digest(spec),
        "artifact_source": "https://huggingface.co/" + spec["repository"] + "/tree/" + spec["revision"],
        "device": "metal", "compute_type": "int4", "temperature": 0,
        "sample_len": 440, "without_timestamps": True,
        "condition_on_previous_text": False, "cache_limit_bytes": 268435456,
        "router_version": "whisper-turbo-single-pass-v1", "timeout_secs": 60,
        "runtime_versions": {name: version(name) for name in ("mlx", "mlx-metal", "mlx-whisper")},
        "machine": platform.machine(), "network_calls": False,
        "setup": "Approved Turbo 4-bit snapshot; cache-only single-model whole-turn decoding; no reference prompt or cascade",
    }
