#!/usr/bin/env python3
"""Explicitly install a checksum-pinned official scanner into a task-owned path."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import platform
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RELEASE = json.loads((ROOT / "configs/security/gitleaks-release.json").read_text())


def extract_binary(archive: bytes, expected_digest: str) -> bytes:
    if hashlib.sha256(archive).hexdigest() != expected_digest:
        raise ValueError("Gitleaks archive checksum mismatch")
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as stream:
        try:
            member = stream.getmember("gitleaks")
        except KeyError as exc:
            raise ValueError("Missing scanner binary member") from exc
        if not member.isfile() or not 1 <= member.size <= 64000000:
            raise ValueError("Invalid scanner binary member")
        source = stream.extractfile(member)
        if source is None:
            raise ValueError("Missing scanner binary")
        return source.read()


def install(destination: Path) -> None:
    system = platform.system().lower()
    machine = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "x64"}.get(platform.machine())
    target = f"{system}_{machine}"
    if target not in RELEASE["archives"]:
        raise ValueError("Scanner platform has no reviewed checksum")
    version = RELEASE["version"]
    url = f"https://github.com/gitleaks/gitleaks/releases/download/v{version}/gitleaks_{version}_{target}.tar.gz"
    request = urllib.request.Request(url, headers={"User-Agent": "Roma-security-verification"})
    with urllib.request.urlopen(request, timeout=30) as response:
        archive = response.read(32000001)
    if len(archive) > 32000000:
        raise ValueError("Scanner archive exceeds size bound")
    binary = extract_binary(archive, RELEASE["archives"][target])
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_symlink():
        raise ValueError("Scanner destination must not be a symlink")
    temporary = destination.with_suffix(".download")
    with temporary.open("xb") as stream:
        stream.write(binary)
    temporary.chmod(0o700)
    temporary.replace(destination)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    try:
        install(args.destination)
    except (OSError, ValueError, tarfile.TarError):
        print(json.dumps({"installed": False, "error": "scanner-installation-failed"}))
        return 1
    print(json.dumps({"installed": True, "version": RELEASE["version"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
