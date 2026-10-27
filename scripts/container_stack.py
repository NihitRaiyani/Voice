"""One-command isolated mock stack; never loads the repository's private .env.

Use `python scripts/container_stack.py up`. Durable volumes survive `down`.
Model/native profiles intentionally refuse the retained Mac-only MLX on Linux.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SECRETS = (
    "postgres_password",
    "redis_password",
    "auth_jwt_secret",
    "pii_hash_key",
    "pii_encryption_key",
    "metrics_token",
    "grafana_password",
)


def _validate_private_file(path: Path) -> None:
    if path.is_symlink():
        raise ValueError("Secret files must be private regular files")
    info = path.stat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_size < 32
    ):
        raise ValueError("Secret files must be private owner-only mode 0600")


def initialize(state: Path, *, has_retained_volumes: bool = False) -> None:
    """Create once; missing keys always require restoring the original material.

    A complete pre-marker keyset can be adopted without rotation. The Docker
    volume probe is required for CLI startup even if the local directory vanished.
    """
    if state.is_symlink():
        raise ValueError("Runtime directory must be private and cannot be a symlink")
    present = [
        name for name in SECRETS if (state / name).exists() or (state / name).is_symlink()
    ]
    marker = state / ".initialized"
    if len(present) != len(SECRETS) and (
        present or marker.exists() or marker.is_symlink() or has_retained_volumes
    ):
        raise ValueError(
            "Missing retained secrets: restore the original keyset; never regenerate"
        )
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    if state.stat().st_uid != os.getuid() or stat.S_IMODE(state.stat().st_mode) != 0o700:
        raise ValueError("Runtime directory must be private (owner-only mode 0700)")
    # Validate every existing file before writing anything, including adoption.
    for name in present:
        _validate_private_file(state / name)
    if marker.exists() or marker.is_symlink():
        _validate_private_file(marker)
        if marker.read_text() != "roma-container-secrets-initialized-v1\n":
            raise ValueError(
                "Invalid initialization marker; restore the original runtime state"
            )
    for name in SECRETS:
        path = state / name
        if not present:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as output:
                output.write(secrets.token_hex(32) + "\n")
    if not marker.exists():
        fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as output:
            output.write("roma-container-secrets-initialized-v1\n")


def retained_volumes(command: list[str], env: dict[str, str], project: str) -> bool:
    """Fail closed if daemon discovery fails; inspect only this project's labels."""
    result = subprocess.run(
        [
            command[0],
            "volume",
            "ls",
            "--filter",
            f"label=com.docker.compose.project={project}",
            "--format",
            "{{.Name}}",
        ],
        cwd=ROOT,
        env=env,
        check=True,
        timeout=15,
        text=True,
        capture_output=True,
    )
    return bool(result.stdout.strip())


def compose_command(state: Path, project: str) -> list[str]:
    if not re.fullmatch(r"roma-stack-[a-z0-9][a-z0-9-]{0,40}", project):
        raise ValueError("Use a dedicated roma-stack- project prefix")
    docker = shutil.which("docker") or "/usr/local/bin/docker"
    return [
        docker,
        "compose",
        "--env-file",
        os.devnull,
        "--project-name",
        project,
        "--file",
        str(ROOT / "compose.stack.yaml"),
    ]


def compose_environment(state: Path, image_tag: str) -> dict[str, str]:
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}", image_tag):
        raise ValueError("Invalid image tag")
    # No provider credentials, DATABASE_URL, COMPOSE_FILE or repository .env.
    env = {
        key: value
        for key, value in os.environ.items()
        if key
        in {
            "PATH",
            "HOME",
            "TMPDIR",
            "DOCKER_HOST",
            "DOCKER_CONTEXT",
            "DOCKER_CONFIG",
            "DOCKER_TLS_VERIFY",
            "DOCKER_CERT_PATH",
        }
    }
    env.update(
        STACK_STATE_DIR=str(state.resolve()),
        ROMA_IMAGE_TAG=image_tag,
        ROMA_WEIGHTS_DIR=str(state.resolve() / "no-models"),
    )
    return env


def run(command: list[str], env: dict[str, str], *, timeout=600) -> None:
    subprocess.run(command, cwd=ROOT, env=env, check=True, timeout=timeout)


def smoke(command: list[str], env: dict[str, str]) -> None:
    run(
        [*command, "exec", "-T", "api", "python", "-m", "roma.container_runtime", "smoke"],
        env,
        timeout=45,
    )
    metrics_url = "http://127.0.0.1:18020/metrics"
    try:
        with urllib.request.urlopen(metrics_url, timeout=5):
            raise RuntimeError("Metrics must require authentication over the bridge")
    except urllib.error.HTTPError as error:
        if error.code != 401:
            raise RuntimeError("Unexpected private metrics response") from None
    state = Path(env["STACK_STATE_DIR"])
    metrics_token = (state / "metrics_token").read_text().strip()
    request = urllib.request.Request(
        metrics_url, headers={"Authorization": "Bearer " + metrics_token}
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        if response.status != 200:
            raise RuntimeError("Authenticated metrics smoke failed")
    password = (state / "grafana_password").read_text().strip()
    basic_auth = base64.b64encode(("roma:" + password).encode()).decode()
    request = urllib.request.Request(
        "http://127.0.0.1:13000/api/datasources/uid/roma-prometheus/health",
        headers={"Authorization": "Basic " + basic_auth},
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        if response.status != 200 or json.load(response).get("status") != "OK":
            raise RuntimeError("Grafana datasource smoke failed")
    for url in ("http://127.0.0.1:19090/-/ready", "http://127.0.0.1:13000/api/health"):
        with urllib.request.urlopen(url, timeout=5) as response:
            if response.status != 200:
                raise RuntimeError("Observability smoke failed")
    deadline = time.monotonic() + 20
    while True:
        with urllib.request.urlopen(
            "http://127.0.0.1:19090/api/v1/targets", timeout=5
        ) as response:
            targets = json.load(response)["data"]["activeTargets"]
        if targets and all(target["health"] == "up" for target in targets):
            break
        if time.monotonic() >= deadline:
            raise RuntimeError("Prometheus target did not become healthy")
        time.sleep(1)
    print("Mock API, SQL migration, Redis, worker, Prometheus scrape and Grafana smoke passed.")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("up", "down", "smoke", "config", "rollback"))
    parser.add_argument("--project", default="roma-stack-local")
    parser.add_argument("--state", type=Path, default=ROOT / "var/container-stack/local")
    parser.add_argument("--image-tag", default="local")
    parser.add_argument(
        "--previous-tag", help="Existing locally built compatible app image tag"
    )
    args = parser.parse_args(argv)
    state = args.state.absolute()
    command = compose_command(state, args.project)
    env = compose_environment(state, args.image_tag)
    if args.action == "config":
        run([*command, "config", "--quiet"], env, timeout=20)
    elif args.action == "down":
        run([*command, "down", "--remove-orphans"], env, timeout=120)  # Never --volumes.
    elif args.action == "up":
        initialize(state, has_retained_volumes=retained_volumes(command, env, args.project))
        if shutil.disk_usage(ROOT).free < 5 * 1024**3:
            raise RuntimeError(
                "At least 5 GiB free disk required for bounded mock image builds"
            )
        run([command[0], "info", "--format", "{{.MemTotal}}"], env, timeout=15)
        run([*command, "build", "--pull"], env)
        # Schema mutation is a distinct controlled one-shot, not an API startup side effect.
        run([*command, "up", "-d", "--wait", "--wait-timeout", "120", "postgres", "redis"], env)
        run([*command, "run", "--rm", "migrator"], env, timeout=120)
        run([*command, "up", "-d", "--wait", "--wait-timeout", "120"], env, timeout=180)
        smoke(command, env)
    elif args.action == "smoke":
        smoke(command, env)
    else:
        if not args.previous_tag:
            parser.error("rollback requires --previous-tag; no schema downgrade is automatic")
        previous_env = compose_environment(state, args.previous_tag)
        run(
            [
                *command,
                "up",
                "-d",
                "--no-build",
                "--pull",
                "never",
                "--no-deps",
                "--wait",
                "--wait-timeout",
                "90",
                "api",
                "worker",
            ],
            previous_env,
            timeout=120,
        )
        smoke(command, previous_env)
        print("Application image rollback passed; durable schema/volumes preserved.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, subprocess.SubprocessError, OSError):
        # Provider errors/connection strings/secret file content never reach logs.
        print(
            "Stack operation failed; inspect service health with the explicit project/config.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
