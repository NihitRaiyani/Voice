#!/usr/bin/env python3
"""Opt-in official Indic-TTS downloads, bounded parallel ranges and verified cache.

Runtime installation is separate. SHA256 records downloaded identities; no publisher
checksum is available, so this does not independently authenticate model weights.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import http.client
import json
import os
import re
import shutil
import stat
import threading
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

RELEASE = "v1-checkpoints-release"
BASE = f"https://github.com/AI4Bharat/Indic-TTS/releases/download/{RELEASE}"
FILES = {
    "tts_checkpoint": "fastpitch/best_model.pth",
    "tts_config_path": "fastpitch/config.json",
    "tts_speakers_file": "fastpitch/speakers.pth",
    "vocoder_checkpoint": "hifigan/best_model.pth",
    "vocoder_config": "hifigan/config.json",
}
MAX_ARCHIVE = 1_700_000_000
MAX_MEMBER = 2_000_000_000
CHUNK = 1024 * 1024
SEGMENT = 4 * CHUNK
RESERVE = 128 * CHUNK
LANGUAGES = {"gu", "hi", "en"}


class SetupError(ValueError):
    """Fixed operator-safe validation messages without remote body/URL details."""


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _json(path: Path) -> dict:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
        raise SetupError("Expected bounded regular setup metadata")
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise SetupError("Expected setup metadata object")
    return value


def _write_json(path: Path, value: dict) -> None:
    staging = path.with_suffix(path.suffix + ".partial")
    if staging.is_symlink() or path.is_symlink():
        raise SetupError("Setup metadata must not be a symlink")
    staging.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    staging.replace(path)


def _sha256(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _safe_path(root: Path, relative: str) -> Path:
    path = root / relative
    if not path.resolve().is_relative_to(root.resolve()):
        raise SetupError("Setup artifact escapes its output directory")
    for component in (path, *path.parents):
        if component == root:
            break
        if component.is_symlink():
            raise SetupError("Setup artifacts/parents must not be symlinks")
    return path


def load_manifest(root: Path) -> dict:
    path = root / "manifest.json"
    if not path.exists() and not path.is_symlink():
        return {
            "schema": "roma-indic-tts-v1",
            "runtime_distribution": "TTS",
            "runtime_version": "0.22.0",
            "languages": {},
        }
    manifest = _json(path)
    if (
        manifest.get("schema") != "roma-indic-tts-v1"
        or manifest.get("runtime_distribution") != "TTS"
        or manifest.get("runtime_version") != "0.22.0"
        or not isinstance(manifest.get("languages"), dict)
        or not set(manifest["languages"]) <= LANGUAGES
    ):
        raise SetupError("Incompatible existing manifest")
    for language, entry in manifest["languages"].items():
        if (
            not isinstance(entry, dict)
            or entry.get("voices") != ["female", "male"]
            or entry.get("source") != f"{BASE}/{language}.zip"
            or not _sha256(entry.get("archive_sha256"))
            or not isinstance(entry.get("files"), dict)
            or set(entry["files"]) != set(FILES)
        ):
            raise SetupError("Invalid existing language manifest")
        for key, suffix in FILES.items():
            artifact = entry["files"][key]
            if (
                not isinstance(artifact, dict)
                or artifact.get("path") != f"{language}/{suffix}"
                or not _sha256(artifact.get("sha256"))
            ):
                raise SetupError("Invalid existing artifact identity/path")
            _safe_path(root, artifact["path"])  # validate before any cached file read/hash.
    return manifest


def validate_url(url: str) -> str:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
        or parsed.fragment
        or any(ord(char) <= 32 or ord(char) == 127 for char in url)
    ):
        raise SetupError("Checkpoint URL must use the reviewed HTTPS origins")
    if parsed.hostname == "github.com":
        if parsed.query or url not in {f"{BASE}/{language}.zip" for language in LANGUAGES}:
            raise SetupError("Only reviewed official checkpoint release URLs are allowed")
    elif parsed.hostname == "release-assets.githubusercontent.com":
        if not parsed.path.startswith("/github-production-release-asset/"):
            raise SetupError("Unexpected GitHub release asset path")
    else:
        raise SetupError("Checkpoint redirect left the reviewed GitHub origins")
    return url


class OfficialRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, newurl):
        validate_url(newurl)
        return super().redirect_request(request, response, code, message, headers, newurl)


class CheckpointHTTPSConnection(http.client.HTTPSConnection):
    """Skip unresponsive CDN addresses promptly while allowing streamed reads."""

    def connect(self):
        read_timeout = self.timeout
        self.timeout = 5
        try:
            super().connect()
        finally:
            self.timeout = read_timeout
        self.sock.settimeout(read_timeout)


class CheckpointHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, request):
        return self.do_open(CheckpointHTTPSConnection, request, context=self._context)


def _opener():
    # Do not inherit a local proxy or credentials. Redirect checks run before contact.
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}), OfficialRedirect(), CheckpointHTTPSHandler()
    )


def _range_response(response, start: int, end: int, total: int | None = None) -> int:
    validate_url(response.geturl())
    match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("Content-Range", ""))
    if response.status != 206 or match is None:
        raise SetupError("Checkpoint server must honor exact byte ranges")
    left, right, size = map(int, match.groups())
    if (
        (left, right) != (start, end)
        or not 0 < size <= MAX_ARCHIVE
        or right >= size
        or (total is not None and size != total)
    ):
        raise SetupError("Checkpoint Content-Range identity/size differs")
    if response.headers.get(
        "Content-Encoding", "identity"
    ).lower() != "identity" or response.headers.get("Content-Length") != str(end - start + 1):
        raise SetupError("Checkpoint response must contain exactly the uncompressed range")
    return size


def _request(opener, url: str, start: int, end: int, validator: str = ""):
    validate_url(url)
    headers = {
        "User-Agent": "Roma-local-TTS-setup/2",
        "Accept-Encoding": "identity",
        "Range": f"bytes={start}-{end}",
    }
    if validator:
        headers["If-Range"] = validator
    return opener.open(urllib.request.Request(url, headers=headers), timeout=60)


def download_archive(
    url: str,
    destination: Path,
    *,
    workers: int = 8,
    resume: bool = False,
    opener_factory=None,
    segment_bytes: int = SEGMENT,
) -> Path:
    """Stream exact ranges into a sparse file; retain hash-checked resume segments.

    Only the initial probe follows the fixed release redirect; worker requests use
    that resolved asset URL. Each worker owns one descriptor and one disjoint range.
    Failed/interrupted runs retain completed segment metadata for explicit resume.
    """
    if (
        type(workers) is not int
        or not 1 <= workers <= 16
        or type(segment_bytes) is not int
        or not 1 <= segment_bytes <= SEGMENT
    ):
        raise SetupError("Choose one to sixteen workers and a bounded segment size")
    validate_url(url)
    factory = opener_factory or _opener
    with _request(factory(), url, 0, 0) as response:
        total = _range_response(response, 0, 0)
        if len(response.read(2)) != 1:
            raise SetupError("Checkpoint probe body differs from its range")
        resolved = response.geturl()
        etag = response.headers.get("ETag", "")
        validator = (
            etag
            if etag and not etag.startswith("W/")
            else response.headers.get("Last-Modified", "")
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    if (
        destination.is_symlink()
        or destination.with_suffix(destination.suffix + ".json").is_symlink()
    ):
        raise SetupError("Download checkpoint must not be a symlink")
    metadata_path = destination.with_suffix(destination.suffix + ".json")
    identity = {
        "schema": "roma-tts-download-v1",
        "url": url,
        "bytes": total,
        "validator": validator,
        "segment_bytes": segment_bytes,
    }
    progress = {**identity, "segments": {}}
    if resume and metadata_path.exists() and destination.exists():
        saved = _json(metadata_path)
        if (
            any(
                saved.get(key) != value
                for key, value in identity.items()
                if key != "segment_bytes"
            )
            or destination.stat().st_size != total
            or not isinstance(saved.get("segments"), dict)
            or (saved.get("segment_bytes") != segment_bytes and saved["segments"])
        ):
            raise SetupError("Resume archive/source identity differs")
        # A previous run with zero completed ranges has no segment layout to preserve.
        progress = {**saved, "segment_bytes": segment_bytes}
    elif destination.exists() or metadata_path.exists():
        raise SetupError(
            "Partial download exists; use --resume-downloads or remove it explicitly"
        )
    if shutil.disk_usage(destination.parent).free < total + RESERVE:
        raise RuntimeError("Insufficient free disk space for the bounded archive")
    if not destination.exists():
        with destination.open("xb") as stream:
            stream.truncate(total)  # sparse allocation, never a multi-GB in-memory buffer.
    count = (total + segment_bytes - 1) // segment_bytes
    # Validate claimed completed bytes, so an interrupted write cannot be skipped.
    for index, expected_hash in list(progress["segments"].items()):
        if not index.isdecimal() or not 0 <= int(index) < count or not _sha256(expected_hash):
            raise SetupError("Invalid resume segment identity")
        start = int(index) * segment_bytes
        length = min(segment_bytes, total - start)
        with destination.open("rb") as stream:
            stream.seek(start)
            checksum = hashlib.sha256()
            remaining = length
            while remaining:
                chunk = stream.read(min(CHUNK, remaining))
                if not chunk:
                    raise SetupError("Resume archive is truncated")
                checksum.update(chunk)
                remaining -= len(chunk)
            actual = checksum.hexdigest()
        if actual != expected_hash:
            del progress["segments"][index]
    _write_json(metadata_path, progress)
    lock, stopped = threading.Lock(), threading.Event()

    def transfer_once(index: int) -> None:
        start = index * segment_bytes
        end = min(total, start + segment_bytes) - 1
        checksum = hashlib.sha256()
        if stopped.is_set():
            return
        with (
            _request(factory(), resolved, start, end, validator) as response,
            destination.open("r+b") as stream,
        ):
            _range_response(response, start, end, total)
            if validator:
                actual_validator = (
                    response.headers.get("ETag")
                    if validator == etag
                    else response.headers.get("Last-Modified")
                )
                if actual_validator != validator:
                    raise SetupError("Checkpoint resource changed during download")
            stream.seek(start)
            remaining = end - start + 1
            while remaining:
                if stopped.is_set():
                    return
                chunk = response.read(min(CHUNK, remaining))
                if not chunk or len(chunk) > remaining:
                    raise SetupError("Checkpoint range body is truncated or oversized")
                stream.write(chunk)
                checksum.update(chunk)
                remaining -= len(chunk)
            if response.read(1):
                raise SetupError("Checkpoint range body exceeds its declared length")
            stream.flush()
            os.fsync(stream.fileno())
        with lock:
            progress["segments"][str(index)] = checksum.hexdigest()
            _write_json(metadata_path, progress)
            completed = len(progress["segments"])
            print(
                f"download: {completed}/{count} verified ranges ({round(completed * segment_bytes / CHUNK)} MiB checkpointed)",
                flush=True,
            )

    def transfer(index: int) -> None:
        for attempt in range(1, 4):
            try:
                transfer_once(index)
                return
            except (OSError, http.client.HTTPException) as exc:
                if isinstance(exc, urllib.error.HTTPError) and exc.code not in {
                    429,
                    500,
                    502,
                    503,
                    504,
                }:
                    raise
                print(
                    f"range {index}: transport {type(exc).__name__}, attempt {attempt}/3",
                    flush=True,
                )
                if attempt == 3:
                    raise SetupError(
                        "Range transport failed after three bounded attempts"
                    ) from None
                if stopped.wait(attempt):
                    return

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=workers)
    try:
        futures = [
            executor.submit(transfer, index)
            for index in range(count)
            if str(index) not in progress["segments"]
        ]
        for future in concurrent.futures.as_completed(futures):
            future.result()
    finally:
        stopped.set()
        executor.shutdown(wait=True, cancel_futures=True)
    if len(progress["segments"]) != count:
        raise SetupError("Checkpoint download did not complete")
    return destination


def extract_artifacts(archive: Path, root: Path, language: str) -> dict:
    if language not in LANGUAGES:
        raise SetupError("Unsupported checkpoint language")
    entries = {}
    with zipfile.ZipFile(archive) as source:
        members = {}
        for key, suffix in FILES.items():
            matches = [
                item
                for item in source.infolist()
                if item.filename.rstrip("/").endswith(f"/{suffix}") and not item.is_dir()
            ]
            bound = (
                MAX_MEMBER
                if key.endswith("checkpoint")
                else 16 * CHUNK
                if key == "tts_speakers_file"
                else 2 * CHUNK
            )
            if (
                len(matches) != 1
                or not 0 < matches[0].file_size <= bound
                or matches[0].flag_bits & 1
                or stat.S_ISLNK(matches[0].external_attr >> 16)
            ):
                raise SetupError(f"Missing, duplicate, linked, encrypted or oversized {key}")
            members[key] = matches[0]
        if (
            shutil.disk_usage(root).free
            < sum(item.file_size for item in members.values()) + RESERVE
        ):
            raise RuntimeError("Insufficient free disk space for checkpoint extraction")
        for key, suffix in FILES.items():
            destination = _safe_path(root, f"{language}/{suffix}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            staging = _safe_path(root, f"{language}/{suffix}.partial")
            try:
                with source.open(members[key]) as reader, staging.open("wb") as writer:
                    remaining = members[key].file_size
                    while chunk := reader.read(min(CHUNK, remaining + 1)):
                        if len(chunk) > remaining:
                            raise SetupError("Expanded checkpoint exceeds its declared bound")
                        writer.write(chunk)
                        remaining -= len(chunk)
                    if remaining:
                        raise SetupError("Expanded checkpoint is truncated")
                staging.replace(destination)
            finally:
                staging.unlink(missing_ok=True)
            entries[key] = {
                "path": destination.relative_to(root).as_posix(),
                "sha256": digest(destination),
            }
    return entries


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-download", action="store_true")
    parser.add_argument("--output", type=Path, default=Path("var/roma/indic-tts"))
    parser.add_argument(
        "--languages", nargs="+", choices=["gu", "hi", "en"], default=["gu", "hi", "en"]
    )
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--resume-downloads", action="store_true")
    args = parser.parse_args()
    if not args.allow_download:
        parser.error("Official checkpoint downloads require --allow-download")
    if not 1 <= args.workers <= 16:
        parser.error("Choose one to sixteen download workers")
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest(root)
    for language in dict.fromkeys(args.languages):
        existing = manifest["languages"].get(language)
        if existing and all(
            (root / value["path"]).is_file() and digest(root / value["path"]) == value["sha256"]
            for value in existing["files"].values()
        ):
            print(f"{language}: verified cached artifacts", flush=True)
            continue
        if shutil.disk_usage(root).free < 5_000_000_000:
            raise RuntimeError("At least 5 GB free space required for archive and extraction")
        archive = _safe_path(root, f".downloads/{language}.zip.part")
        print(
            f"{language}: downloading official {RELEASE} archive with {args.workers} workers",
            flush=True,
        )
        download_archive(
            f"{BASE}/{language}.zip",
            archive,
            workers=args.workers,
            resume=args.resume_downloads,
        )
        archive_hash = digest(archive)
        entries = extract_artifacts(archive, root, language)
        manifest["languages"][language] = {
            "voices": ["female", "male"],
            "files": entries,
            "source": f"{BASE}/{language}.zip",
            "archive_sha256": archive_hash,
        }
        _write_json(root / "manifest.json", manifest)
        archive.unlink()
        archive.with_suffix(archive.suffix + ".json").unlink()
        print(f"{language}: extracted five files and recorded SHA-256 identities", flush=True)
    print(f"manifest: {root / 'manifest.json'}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(
            "Setup interrupted; completed ranges retained. Resume with --resume-downloads.",
            flush=True,
        )
        raise SystemExit(130) from None
    except Exception as exc:  # noqa: BLE001 -- never print signed release URLs or remote bodies.
        safe_detail = (
            str(exc)
            if isinstance(exc, SetupError)
            else "Check release availability, manifest, disk space and resume options"
        )
        print(f"Setup failed ({type(exc).__name__}): {safe_detail}", flush=True)
        raise SystemExit(1) from None
