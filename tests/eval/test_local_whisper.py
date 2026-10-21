import hashlib
import json

import pytest
from roma.eval import local_whisper


@pytest.fixture
def artifacts(tmp_path, monkeypatch):
    folder = tmp_path / "base"
    folder.mkdir()
    files = {}
    for name in ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt"):
        data = name.encode()
        (folder / name).write_bytes(data)
        files[name] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    spec = tmp_path / "spec.json"
    spec.write_text(
        json.dumps(
            {"repository": "Systran/faster-whisper-base", "revision": "a" * 40, "files": files}
        )
    )
    monkeypatch.setattr(local_whisper, "SPEC", spec)
    monkeypatch.setattr(local_whisper, "version", lambda name: "test-version")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    return folder


def test_local_provenance_hashes_every_artifact_without_loading_a_model(artifacts):
    detail = local_whisper.local_base_details(artifacts)
    assert detail["network_calls"] is False
    assert detail["compute_type"] == "int8" and detail["cpu_threads"] == 2
    assert detail["model_sha256"] == hashlib.sha256(b"model.bin").hexdigest()
    assert str(artifacts) not in json.dumps(detail)


@pytest.mark.parametrize("kind", ["checksum", "missing", "extra", "symlink", "online"])
def test_local_artifact_drift_rejected_before_model_loading(artifacts, monkeypatch, kind):
    weight = artifacts / "model.bin"
    if kind == "checksum":
        weight.write_bytes(b"wrong.bin")
    elif kind == "missing":
        weight.unlink()
    elif kind == "extra":
        (artifacts / "preprocessor_config.json").write_text("{}")
    elif kind == "symlink":
        target = artifacts.parent / "weight.bin"
        weight.rename(target)
        weight.symlink_to(target)
    else:
        monkeypatch.delenv("HF_HUB_OFFLINE")
    with pytest.raises(ValueError):
        local_whisper.local_base_details(artifacts)


def test_mlx_artifacts_are_verified_and_have_distinct_runtime(artifacts, monkeypatch, tmp_path):
    from roma.providers.ai import whisper_mlx
    folder = tmp_path / "mlx-model"
    folder.mkdir()
    source = json.loads(local_whisper.SPEC.read_text())
    source.update(repository="mlx-community/whisper-large-v3-turbo-4bit", revision="c" * 40)
    source["files"]["weights.safetensors"] = source["files"].pop("model.bin")
    for name in source["files"]:
        (folder / name).write_bytes(b"model.bin" if name == "weights.safetensors" else name.encode())
    spec = tmp_path / "mlx-spec.json"
    spec.write_text(json.dumps(source))
    monkeypatch.setattr(whisper_mlx, "ARTIFACT_SPEC", spec)
    detail = local_whisper.local_mlx_details(folder)
    assert detail["compute_type"] == "int4" and detail["device"] == "metal"
    assert detail["sample_len"] == 440 and detail["network_calls"] is False
    (folder / "weights.safetensors").write_bytes(b"wrong.bin")
    with pytest.raises(ValueError, match="checksum"):
        local_whisper.local_mlx_details(folder)
