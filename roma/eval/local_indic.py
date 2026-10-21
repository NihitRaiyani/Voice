"""Identity and cache-only preflight for the owner-approved IndicConformer lab."""

import hashlib
import json
import os
import platform
from importlib.metadata import version
from pathlib import Path

from roma.eval.asr_benchmark import digest

SPEC = Path(__file__).resolve().parents[2] / "benchmarks/asr/indic-conformer-local-install.json"


def local_indic_details(config) -> dict:
    if os.environ.get("HF_HUB_OFFLINE") != "1" or not config.indic_trust_remote_code:
        raise ValueError("Local Indic benchmark requires offline mode and reviewed-code opt-in")
    spec = json.loads(SPEC.read_text())
    if (config.indic_model_id, config.indic_model_revision) != (
        spec["repository"], spec["revision"]
    ):
        raise ValueError("Local Indic benchmark requires the approved immutable revision")
    from huggingface_hub import constants, snapshot_download

    if not constants.HF_HUB_OFFLINE:
        raise ValueError("Restart Python with HF_HUB_OFFLINE=1")
    snapshot = Path(snapshot_download(
        config.indic_model_id, revision=config.indic_model_revision, local_files_only=True
    ))
    if not snapshot.is_dir() or snapshot.name != spec["revision"]:
        raise ValueError("Local Indic snapshot is unavailable")
    identities: dict[str, dict] = {}
    for path in sorted(snapshot.rglob("*")):
        if not path.is_file():
            continue
        checksum = hashlib.sha256()
        with path.open("rb") as stream:
            while block := stream.read(1024 * 1024):
                checksum.update(block)
        identities[path.relative_to(snapshot).as_posix()] = {
            "bytes": path.stat().st_size, "sha256": checksum.hexdigest()
        }
    if (
        len(identities) != spec["snapshot"]["files"]
        or sum(item["bytes"] for item in identities.values()) != spec["snapshot"]["bytes"]
    ):
        raise ValueError("Local Indic artifact set/size differs from the installed snapshot")
    return {
        "model_name": spec["repository"],
        "model_revision": spec["revision"],
        "model_sha256": digest(identities),
        "checksum_scope": "sha256 of sorted relative-path, byte-size and sha256 map for all snapshot files",
        "artifact_manifest_sha256": digest({
            "repository": spec["repository"], "revision": spec["revision"], "files": identities
        }),
        "artifact_source": "https://huggingface.co/" + spec["repository"] + "/tree/" + spec["revision"],
        "device": "cpu",
        "compute_type": "float32",
        "decoder": config.indic_decoder,
        "cpu_threads": "runtime-default",
        "num_workers": 1,
        "timeout_secs": 60,
        "runtime_versions": {
            name: version(name)
            for name in ("torch", "transformers", "onnxruntime", "huggingface-hub", "numpy")
        },
        "machine": platform.machine(),
        "network_calls": False,
        "supported_languages": ["gu", "hi", "gu-en", "hi-en"],
        "setup": "Approved pinned IndicConformer snapshot provisioned separately; cache-only inference",
    }
