#!/usr/bin/env python3
"""Scan all Git history and current Git-visible files without printing secret values."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = json.loads((ROOT / "configs/security/gitleaks-release.json").read_text())["version"]


def scan_repository(repository: Path, binary: Path) -> dict:
    shallow = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "--is-shallow-repository"],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    ).stdout.strip()
    if shallow != "false":
        raise ValueError("Full history scan requires a non-shallow Git repository")
    version = subprocess.run(
        [str(binary), "version"], capture_output=True, text=True, timeout=10, check=True
    ).stdout.strip()
    if version != VERSION:
        raise ValueError("Scanner version differs from the reviewed release")
    findings = []
    with tempfile.TemporaryDirectory(prefix="roma-secret-scan-") as temporary:
        private = Path(temporary)
        tree = private / "worktree"
        tree.mkdir()
        listed = subprocess.run(
            [
                "git",
                "-C",
                str(repository),
                "ls-files",
                "-z",
                "--cached",
                "--others",
                "--exclude-standard",
            ],
            capture_output=True,
            check=True,
        ).stdout
        for filename in set(listed.decode().split("\0")) - {""}:
            source = repository / filename
            if not source.exists() and not source.is_symlink():
                continue
            target = tree / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.is_symlink():
                target.write_text(os.readlink(source))
            elif source.is_file():
                shutil.copyfile(source, target)
        for mode, source, label in (("git", repository, "history"), ("dir", tree, "worktree")):
            report = private / f"{label}.json"
            command = [
                str(binary),
                mode,
                str(source),
                "--redact=100",
                "--no-banner",
                "--no-color",
                "--ignore-gitleaks-allow",
                "--config",
                str(ROOT / "configs/security/gitleaks.toml"),
                "--gitleaks-ignore-path",
                str(ROOT / "configs/security/gitleaksignore"),
                "--report-format=json",
                "--report-path",
                str(report),
                "--max-decode-depth=2",
                "--max-archive-depth=2",
                "--timeout=180",
            ]
            if mode == "git":
                # Git omits merge patches by default, including merge-only secrets.
                command.append("--log-opts=--all --full-history --diff-merges=first-parent")
            result = subprocess.run(command, capture_output=True, timeout=190, check=False)
            if result.returncode not in (0, 1) or not report.exists():
                raise ValueError("Scanner execution failed")
            rows = json.loads(report.read_text()) or []
            if result.returncode and not rows:
                raise ValueError("Scanner execution returned an unexplained failure")
            for row in rows:
                filename = row["File"]
                if filename.startswith(str(tree) + os.sep):
                    filename = str(Path(filename).relative_to(tree))
                findings.append(
                    {
                        "source": label,
                        "rule": row["RuleID"],
                        "file": filename,
                        "line": row["StartLine"],
                        "commit": row.get("Commit", ""),
                        "fingerprint": row.get("Fingerprint", ""),
                    }
                )
    return {"passed": not findings, "scanner": f"gitleaks-{VERSION}", "findings": findings}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=ROOT)
    parser.add_argument(
        "--binary", type=Path, default=Path(shutil.which("gitleaks") or "/unavailable/gitleaks")
    )
    args = parser.parse_args()
    try:
        result = scan_repository(args.repository, args.binary)
    except (OSError, ValueError, subprocess.SubprocessError):
        print(json.dumps({"passed": False, "error": "pinned-scanner-unavailable"}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return int(not result["passed"])


if __name__ == "__main__":
    raise SystemExit(main())
