"""Local benchmark provisioning checks never load a model or access a server."""

import json
import sys
from types import SimpleNamespace

import pytest
from roma.eval import local_indic


@pytest.fixture
def cache(tmp_path, monkeypatch):
    revision = "a" * 40
    snapshot = tmp_path / "snapshots" / revision
    snapshot.mkdir(parents=True)
    (snapshot / "model_onnx.py").write_text("reviewed")
    (snapshot / "config.json").write_text("{}")
    spec = tmp_path / "install.json"
    spec.write_text(json.dumps({
        "repository": "ai4bharat/indic-conformer-600m-multilingual",
        "revision": revision,
        "snapshot": {"files": 2, "bytes": 10},
    }))
    calls = []

    def download(repo_id, **kwargs):
        calls.append((repo_id, kwargs))
        return str(snapshot)

    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setattr(local_indic, "SPEC", spec)
    monkeypatch.setattr(local_indic, "version", lambda name: "test")
    monkeypatch.setitem(sys.modules, "huggingface_hub", SimpleNamespace(
        constants=SimpleNamespace(HF_HUB_OFFLINE=True), snapshot_download=download
    ))
    config = SimpleNamespace(
        indic_model_id="ai4bharat/indic-conformer-600m-multilingual",
        indic_model_revision=revision, indic_trust_remote_code=True, indic_decoder="ctc"
    )
    return config, snapshot, calls


def test_local_indic_provenance_is_pinned_offline_and_path_free(cache):
    config, snapshot, calls = cache
    details = local_indic.local_indic_details(config)
    assert details["network_calls"] is False
    assert details["model_revision"] == config.indic_model_revision
    assert details["compute_type"] == "float32"
    assert calls == [(config.indic_model_id, {
        "revision": config.indic_model_revision, "local_files_only": True
    })]
    assert str(snapshot) not in json.dumps(details)
    first = details["model_sha256"]
    # Even same-size artifact changes get a distinct run identity and cannot resume.
    (snapshot / "config.json").write_text("[]")
    assert local_indic.local_indic_details(config)["model_sha256"] != first


@pytest.mark.parametrize("kind", ["online", "unreviewed", "revision", "missing", "extra", "sdk-online"])
def test_local_indic_preflight_refuses_invalid_setup(cache, monkeypatch, kind):
    config, snapshot, calls = cache
    if kind == "online":
        monkeypatch.delenv("HF_HUB_OFFLINE")
    elif kind == "unreviewed":
        config.indic_trust_remote_code = False
    elif kind == "revision":
        config.indic_model_revision = "b" * 40
    elif kind == "missing":
        (snapshot / "config.json").unlink()
    elif kind == "extra":
        (snapshot / "unexpected").write_text("x")
    else:
        sys.modules["huggingface_hub"].constants.HF_HUB_OFFLINE = False
    with pytest.raises(ValueError):
        local_indic.local_indic_details(config)
    if kind in {"online", "unreviewed", "revision", "sdk-online"}:
        assert not calls
