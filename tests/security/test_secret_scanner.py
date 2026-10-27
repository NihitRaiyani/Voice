"""Scanner supply-chain and redaction checks; real CLI only when explicitly installed."""

import hashlib
import io
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest


def test_scanner_archive_requires_expected_digest_and_extracts_only_binary():
    from scripts.install_gitleaks import extract_binary

    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w:gz") as stream:
        member = tarfile.TarInfo("gitleaks")
        member.size = 4
        stream.addfile(member, io.BytesIO(b"test"))
    payload = archive.getvalue()
    assert extract_binary(payload, hashlib.sha256(payload).hexdigest()) == b"test"
    with pytest.raises(ValueError, match="checksum"):
        extract_binary(payload, "0" * 64)


def test_real_scanner_finds_removed_history_and_new_worktree_secret_without_echo(tmp_path):
    from scripts.scan_secrets import scan_repository

    binary = shutil.which("gitleaks")
    if not binary:
        pytest.skip("Pinned Gitleaks binary is installed explicitly in the security CI job")
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=True)

    git("init")
    git("config", "user.email", "synthetic@example.invalid")
    git("config", "user.name", "Synthetic fixture")
    credential = "ghp_" + "T5z4Qw8Er6Yt2Ui9Op1As3Df7Gh0Jk5Lz8Xc"
    (repo / "removed.txt").write_text(credential)
    git("add", ".")
    git("commit", "-m", "synthetic scanner fixture")
    (repo / "removed.txt").unlink()
    git("add", ".")
    git("commit", "-m", "remove fixture")
    (repo / "tests/core").mkdir(parents=True)
    (repo / "tests/core/test_logging.py").write_text(credential + " # gitleaks:allow")
    result = scan_repository(repo, Path(binary))
    assert result["passed"] is False
    assert {item["source"] for item in result["findings"]} == {"history", "worktree"}
    assert credential not in str(result)
    assert {item["file"] for item in result["findings"]} == {
        "removed.txt",
        "tests/core/test_logging.py",
    }


def test_scanner_rejects_an_archive_with_a_symlink_or_missing_binary():
    from scripts.install_gitleaks import extract_binary

    for name, kind in (("gitleaks", tarfile.SYMTYPE), ("other", tarfile.REGTYPE)):
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w:gz") as stream:
            member = tarfile.TarInfo(name)
            member.type = kind
            member.linkname = "/untrusted"
            stream.addfile(member)
        payload = archive.getvalue()
        with pytest.raises(ValueError):
            extract_binary(payload, hashlib.sha256(payload).hexdigest())


def test_real_scanner_finds_a_removed_secret_introduced_only_in_merge_commit(tmp_path):
    from scripts.scan_secrets import scan_repository

    binary = shutil.which("gitleaks")
    if not binary:
        pytest.skip("Pinned Gitleaks binary is installed explicitly in the security CI job")
    repo = tmp_path / "merge-repo"
    repo.mkdir()

    def git(*args):
        return subprocess.run(
            ["git", "-C", str(repo), *args], capture_output=True, check=True, text=True
        ).stdout.strip()

    git("init", "--initial-branch=main")
    git("config", "user.email", "synthetic@example.invalid")
    git("config", "user.name", "Synthetic fixture")
    (repo / "base.txt").write_text("shared base")
    git("add", ".")
    git("commit", "-m", "base")
    git("checkout", "-b", "side")
    (repo / "side.txt").write_text("side branch")
    git("add", ".")
    git("commit", "-m", "side")
    git("checkout", "main")
    (repo / "main.txt").write_text("main branch")
    git("add", ".")
    git("commit", "-m", "main")
    git("merge", "--no-commit", "--no-ff", "side")
    credential = "ghp_" + "T5z4Qw8Er6Yt2Ui9Op1As3Df7Gh0Jk5Lz8Xc"
    (repo / "merge-only.txt").write_text(credential)
    git("add", ".")
    git("commit", "-m", "merge with synthetic scanner fixture")
    merge_commit = git("rev-parse", "HEAD")
    (repo / "merge-only.txt").unlink()
    git("add", ".")
    git("commit", "-m", "remove merge fixture")

    result = scan_repository(repo, Path(binary))
    assert result["passed"] is False
    assert {item["source"] for item in result["findings"]} == {"history"}
    assert {item["file"] for item in result["findings"]} == {"merge-only.txt"}
    assert {item["commit"] for item in result["findings"]} == {merge_commit}
    assert credential not in str(result)


def test_real_scanner_rejects_incomplete_shallow_history(tmp_path):
    from scripts.scan_secrets import scan_repository

    binary = shutil.which("gitleaks")
    if not binary:
        pytest.skip("Pinned Gitleaks binary is installed explicitly in the security CI job")
    repo = tmp_path / "source"
    repo.mkdir()

    def git(*args):
        subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=True)

    git("init")
    git("config", "user.email", "synthetic@example.invalid")
    git("config", "user.name", "Synthetic fixture")
    (repo / "content.txt").write_text("first revision")
    git("add", ".")
    git("commit", "-m", "first")
    (repo / "content.txt").write_text("second revision")
    git("add", ".")
    git("commit", "-m", "second")
    shallow = tmp_path / "shallow"
    subprocess.run(
        ["git", "clone", "--depth=1", repo.as_uri(), str(shallow)],
        capture_output=True,
        check=True,
    )
    with pytest.raises(ValueError, match="shallow"):
        scan_repository(shallow, Path(binary))
