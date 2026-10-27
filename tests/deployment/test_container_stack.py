"""Section 53 packaging contract; no daemon, providers, private env or models."""

from __future__ import annotations

import importlib.util
import os
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "container_stack", ROOT / "scripts/container_stack.py"
)
assert SPEC and SPEC.loader
stack = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(stack)


def test_private_runtime_material_is_stable_and_not_in_repository(tmp_path):
    state = tmp_path / "stack"
    stack.initialize(state)
    original = (state / "postgres_password").read_bytes()
    stack.initialize(state)
    assert (state / "postgres_password").read_bytes() == original
    assert stat.S_IMODE(state.stat().st_mode) == 0o700
    for filename in stack.SECRETS:
        path = state / filename
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert len(path.read_text().strip()) >= 32
    assert not (state / ".env").exists()


def test_secret_symlink_and_broad_permissions_fail_closed(tmp_path):
    state = tmp_path / "stack"
    stack.initialize(state)
    secret = state / "auth_jwt_secret"
    secret.chmod(0o644)
    with pytest.raises(ValueError, match="private"):
        stack.initialize(state)
    secret.unlink()
    secret.symlink_to(tmp_path / "outside")
    with pytest.raises(ValueError):
        stack.initialize(state)


def test_project_prefix_and_explicit_env_file(tmp_path):
    for name in ("roma", "default", "../roma", "roma-stack-evil;exit"):
        with pytest.raises(ValueError):
            stack.compose_command(tmp_path, name)
    command = stack.compose_command(tmp_path, "roma-stack-test")
    assert command[1:4] == ["compose", "--env-file", os.devnull]
    assert "--project-name" in command
    assert command[-1].endswith("compose.stack.yaml")


def test_build_context_is_allowlist_and_hardening_is_present():
    ignored = (ROOT / ".dockerignore").read_text()
    assert ignored.startswith("**\n")
    assert "!.env" not in ignored
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "COPY . " not in dockerfile
    assert "--locked" in dockerfile and "--no-dev" in dockerfile
    assert "USER 10001:10001" in dockerfile
    compose = (ROOT / "compose.stack.yaml").read_text()
    assert "service_completed_successfully" in compose
    assert "service_healthy" in compose
    assert "privileged: true" not in compose
    assert "network_mode: host" not in compose
    assert "env_file:" not in compose
    assert ":/models:ro" in compose
    assert "capabilities: [gpu]" in compose
    assert "read_only: true" in compose
    assert "127.0.0.1:18020:8020" in compose


def test_compose_config_without_private_env_or_daemon(tmp_path):
    stack.initialize(tmp_path / "stack")
    env = stack.compose_environment(tmp_path / "stack", "test")
    result = subprocess.run(
        [
            *stack.compose_command(tmp_path / "stack", "roma-stack-contract"),
            "config",
            "--quiet",
        ],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode and "not found" in result.stderr:
        pytest.skip("Docker Compose CLI is not installed")
    assert result.returncode == 0, result.stderr


def test_gpu_platform_boundary_fails_before_any_weight_access(monkeypatch):
    from roma.container_runtime import check_model_platform

    monkeypatch.setenv("ROMA_MODEL_ID", "whisper-turbo-mlx-4bit")
    with pytest.raises(RuntimeError, match="Mac"):
        check_model_platform("gpu")


def test_tmpfs_options_remain_one_mount_after_yaml_parse():
    import yaml

    config = yaml.safe_load((ROOT / "compose.stack.yaml").read_text())
    for service in config["services"].values():
        for mount in service.get("tmpfs", []):
            assert mount.startswith("/"), "YAML split tmpfs options into separate mounts"


def test_initializer_can_reset_owner_only_modes_on_repeat_start():
    import yaml

    config = yaml.safe_load((ROOT / "compose.stack.yaml").read_text())
    initializer = config["services"]["secrets-init"]
    assert "FOWNER" in initializer["cap_add"]
    assert initializer.get("network_mode") == "none"


def test_published_services_have_bridge_and_datastores_stay_internal():
    import yaml

    config = yaml.safe_load((ROOT / "compose.stack.yaml").read_text())
    assert config["networks"]["private"]["internal"] is True
    for name in ("api", "prometheus", "grafana"):
        assert "loopback" in config["services"][name]["networks"]
    for name in ("postgres", "redis", "worker"):
        assert config["services"][name]["networks"] == ["private"]


def test_smoke_verifies_private_metrics_and_provisioned_datasource(tmp_path, monkeypatch):
    import io
    import urllib.error

    state = tmp_path / "stack"
    stack.initialize(state)
    requested = []

    class Response(io.BytesIO):
        status = 200

    def response(request, **kwargs):
        url = request if isinstance(request, str) else request.full_url
        authorization = "" if isinstance(request, str) else request.get_header("Authorization")
        requested.append((url, authorization))
        if url.endswith("/metrics") and not authorization:
            raise urllib.error.HTTPError(url, 401, "Unauthorized", {}, None)
        if url.endswith("/api/v1/targets"):
            return Response(b'{"data":{"activeTargets":[{"health":"up"}]}}')
        return Response(b'{"status":"OK","database":"ok"}')

    monkeypatch.setattr(stack, "run", lambda *args, **kwargs: None)
    monkeypatch.setattr(stack.urllib.request, "urlopen", response)
    stack.smoke([], stack.compose_environment(state, "test"))
    assert any(
        url.endswith("/metrics") and auth.startswith("Bearer ") for url, auth in requested
    )
    assert any(
        url.endswith("/roma-prometheus/health") and auth.startswith("Basic ")
        for url, auth in requested
    )


def test_container_helpers_do_not_shadow_the_docker_sdk():
    import docker.errors

    assert issubclass(docker.errors.DockerException, Exception)


def test_partial_keyset_fails_without_replacing_or_creating_keys(tmp_path):
    state = tmp_path / "stack"
    stack.initialize(state)
    previous = {name: (state / name).read_bytes() for name in stack.SECRETS}
    (state / "pii_encryption_key").unlink()
    with pytest.raises(ValueError, match="restore"):
        stack.initialize(state)
    assert not (state / "pii_encryption_key").exists()
    assert all(
        (state / name).read_bytes() == data
        for name, data in previous.items()
        if name != "pii_encryption_key"
    )


def test_lost_keyset_with_retained_volumes_fails_before_key_creation(tmp_path):
    state = tmp_path / "lost"
    with pytest.raises(ValueError, match="restore"):
        stack.initialize(state, has_retained_volumes=True)
    assert not state.exists()


def test_initialized_marker_blocks_regeneration_and_full_legacy_can_be_adopted(tmp_path):
    state = tmp_path / "stack"
    stack.initialize(state)
    marker = state / ".initialized"
    assert marker.exists()
    marker.unlink()
    before = {name: (state / name).read_bytes() for name in stack.SECRETS}
    stack.initialize(state, has_retained_volumes=True)
    assert marker.exists()
    assert all((state / name).read_bytes() == value for name, value in before.items())
    for name in stack.SECRETS:
        (state / name).unlink()
    with pytest.raises(ValueError, match="restore"):
        stack.initialize(state)
    assert not any((state / name).exists() for name in stack.SECRETS)


@pytest.mark.parametrize("action", ["down", "config", "smoke"])
def test_noninitializing_actions_never_generate_runtime_keys(tmp_path, monkeypatch, action):
    state = tmp_path / "absent"
    calls = []
    monkeypatch.setattr(stack, "run", lambda *args, **kwargs: calls.append(args))
    monkeypatch.setattr(stack, "smoke", lambda *args, **kwargs: calls.append(args))
    stack.main([action, "--state", str(state), "--project", "roma-stack-readonly"])
    assert not state.exists()
    assert calls


def test_up_probes_project_volumes_before_initialization(tmp_path, monkeypatch):
    state = tmp_path / "lost"
    monkeypatch.setattr(stack, "retained_volumes", lambda *args: True)
    with pytest.raises(ValueError, match="restore"):
        stack.main(["up", "--state", str(state), "--project", "roma-stack-proof"])
    assert not state.exists()


def test_owned_services_select_explicit_hardened_dockerfiles():
    import yaml

    config = yaml.safe_load((ROOT / "compose.stack.yaml").read_text())
    prefix = (ROOT / "Dockerfile").read_text().split("FROM runtime AS api\n")[0]
    for service_name, target in (
        ("api", "api"),
        ("worker", "worker"),
        ("speech-gpu", "model-contract"),
        ("speech-native-bridge", "model-contract"),
    ):
        build = config["services"][service_name]["build"]
        expected_file = f"docker/Dockerfile.{target}"
        assert build["dockerfile"] == expected_file
        source = (ROOT / expected_file).read_text()
        assert source.startswith(prefix)
        assert f"FROM runtime AS {target}\n" in source
        assert source.count("FROM runtime AS ") == 1
        assert 'CMD ["' + ("gpu" if target == "model-contract" else target) + '"]' in source
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    assert '--file "docker/Dockerfile.${{ matrix.target }}"' in workflow
