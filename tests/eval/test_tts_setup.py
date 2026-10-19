"""Offline transport/extraction checks: no download or model execution."""

import importlib.util
import io
import json
import threading
import urllib.request
import zipfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/setup_indic_tts.py"
SPEC = importlib.util.spec_from_file_location("tts_setup_test", SCRIPT)
setup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(setup)
URL = f"{setup.BASE}/gu.zip"
ASSET = "https://release-assets.githubusercontent.com/github-production-release-asset/123/gu.zip?sig=test"


class Response(io.BytesIO):
    status = 206

    def __init__(self, body, start, end, total):
        super().__init__(body)
        self.headers = {
            "Content-Range": f"bytes {start}-{end}/{total}",
            "Content-Length": str(end - start + 1),
            "ETag": '"official-identity"',
        }

    def geturl(self):
        return ASSET


class FakeServer:
    def __init__(self, data=b"public official weights" * 7, mutate=None):
        self.data, self.mutate = data, mutate
        self.calls = []
        self.lock = threading.Lock()

    def open(self, request, timeout):
        assert timeout == 60
        assert request.headers["Accept-encoding"] == "identity"
        assert "Authorization" not in request.headers
        start, end = map(int, request.headers["Range"].removeprefix("bytes=").split("-"))
        with self.lock:
            self.calls.append((start, end))
        response = Response(self.data[start : end + 1], start, end, len(self.data))
        if (start, end) != (0, 0):
            assert request.headers["If-range"] == '"official-identity"'
            if self.mutate:
                self.mutate(response, start, end)
        return response


def test_parallel_ranges_exact_bytes_and_resumable_hashes(tmp_path):
    server = FakeServer()
    target = tmp_path / "weights.zip.part"
    result = setup.download_archive(
        URL, target, workers=4, segment_bytes=32, opener_factory=lambda: server
    )
    assert result.read_bytes() == server.data
    expected = [
        (start, min(start + 31, len(server.data) - 1))
        for start in range(0, len(server.data), 32)
    ]
    assert sorted(server.calls[1:]) == expected
    server.calls.clear()
    setup.download_archive(
        URL, target, workers=4, segment_bytes=32, resume=True, opener_factory=lambda: server
    )
    assert server.calls == [(0, 0)]  # full data verified locally before skip
    with target.open("r+b") as stream:
        stream.seek(35)
        stream.write(b"tampered")
    server.calls.clear()
    setup.download_archive(
        URL, target, workers=4, segment_bytes=32, resume=True, opener_factory=lambda: server
    )
    assert server.calls == [(0, 0), (32, 63)]
    assert target.read_bytes() == server.data
    with pytest.raises(ValueError, match="Partial download"):
        setup.download_archive(URL, target, opener_factory=lambda: server)


@pytest.mark.parametrize(
    "fault",
    [
        "status",
        "range",
        "size",
        "encoding",
        "length",
        "truncated",
        "oversized",
        "etag",
        "redirect",
    ],
)
def test_range_protocol_faults_fail_without_claiming_complete(tmp_path, fault):
    def mutate(response, start, end):
        if fault == "status":
            response.status = 200
        elif fault == "range":
            response.headers["Content-Range"] = f"bytes {start + 1}-{end}/{len(server.data)}"
        elif fault == "size":
            response.headers["Content-Range"] = f"bytes {start}-{end}/{setup.MAX_ARCHIVE + 1}"
        elif fault == "encoding":
            response.headers["Content-Encoding"] = "gzip"
        elif fault == "length":
            response.headers["Content-Length"] = "9999"
        elif fault == "truncated":
            response.truncate(1)
        elif fault == "oversized":
            response.seek(0, 2)
            response.write(b"extra")
            response.seek(0)
        elif fault == "etag":
            response.headers["ETag"] = '"changed"'
        else:
            response.geturl = lambda: "https://attacker.example/weights.zip"

    server = FakeServer(mutate=mutate)
    target = tmp_path / "bad.part"
    with pytest.raises(ValueError):
        setup.download_archive(
            URL, target, workers=1, segment_bytes=32, opener_factory=lambda: server
        )
    metadata = json.loads(target.with_suffix(".part.json").read_text())
    assert metadata["segments"] == {}


def test_interrupted_segments_resume_only_completed_ranges(tmp_path):
    server = FakeServer()

    def fail(response, start, end):
        if start == 32:
            raise OSError("offline interruption")

    server.mutate = fail
    target = tmp_path / "resume.part"
    with pytest.raises(
        setup.SetupError, match="Range transport failed after three bounded attempts"
    ):
        setup.download_archive(
            URL, target, workers=1, segment_bytes=32, opener_factory=lambda: server
        )
    assert json.loads(target.with_suffix(".part.json").read_text())["segments"]["0"]
    server.mutate = None
    server.calls.clear()
    setup.download_archive(
        URL, target, workers=1, segment_bytes=32, resume=True, opener_factory=lambda: server
    )
    assert (0, 31) not in server.calls
    assert target.read_bytes() == server.data


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/AI4Bharat/Indic-TTS/releases/download/v1-checkpoints-release/gu.zip",
        "https://github.com/attacker/release.zip",
        "https://github.com/AI4Bharat/Indic-TTS/releases/download/v1-checkpoints-release/gu.zip?token=x",
        "https://release-assets.githubusercontent.com.attacker.example/github-production-release-asset/1",
        "https://user:password@release-assets.githubusercontent.com/github-production-release-asset/1",
        "https://release-assets.githubusercontent.com:8443/github-production-release-asset/1",
        "https://release-assets.githubusercontent.com/unreviewed/1",
    ],
)
def test_rejects_unreviewed_origins_before_request(url):
    with pytest.raises(ValueError):
        setup.validate_url(url)
    with pytest.raises(ValueError):
        setup.OfficialRedirect().redirect_request(
            urllib.request.Request(URL), None, 302, "Found", {}, url
        )


def archive(tmp_path, duplicate=False):
    path = tmp_path / "archive.zip"
    with zipfile.ZipFile(path, "w") as zipped:
        for suffix in setup.FILES.values():
            zipped.writestr("official/" + suffix, b"test " + suffix.encode())
        if duplicate:
            zipped.writestr("other/fastpitch/config.json", b"duplicate")
    return path


def test_extract_exact_five_artifacts_and_manifest_validation(tmp_path):
    root = tmp_path / "models"
    root.mkdir()
    path = archive(tmp_path)
    entries = setup.extract_artifacts(path, root, "gu")
    assert set(entries) == set(setup.FILES)
    assert all(set(entry) == {"path", "sha256"} for entry in entries.values())
    manifest = setup.load_manifest(root)
    manifest["languages"]["gu"] = {
        "voices": ["female", "male"],
        "files": entries,
        "source": URL,
        "archive_sha256": setup.digest(path),
    }
    setup._write_json(root / "manifest.json", manifest)
    assert setup.load_manifest(root) == manifest
    assert manifest["runtime_version"] == "0.22.0"
    manifest["languages"]["gu"]["files"]["tts_checkpoint"]["path"] = "../../private.pth"
    setup._write_json(root / "manifest.json", manifest)
    with pytest.raises(ValueError, match="identity/path"):
        setup.load_manifest(root)


def test_manifest_symlink_rejected_before_cache_hash_read(tmp_path):
    root = tmp_path / "models"
    root.mkdir()
    (root / "gu").symlink_to(tmp_path)
    manifest = setup.load_manifest(root)
    manifest["languages"]["gu"] = {
        "voices": ["female", "male"],
        "files": {
            key: {"path": f"gu/{suffix}", "sha256": "a" * 64}
            for key, suffix in setup.FILES.items()
        },
        "source": URL,
        "archive_sha256": "a" * 64,
    }
    setup._write_json(root / "manifest.json", manifest)
    with pytest.raises(ValueError, match=r"symlink|escapes"):
        setup.load_manifest(root)


def test_duplicate_zip_artifacts_rejected_without_extraction(tmp_path):
    root = tmp_path / "models"
    root.mkdir()
    with pytest.raises(ValueError, match="duplicate"):
        setup.extract_artifacts(archive(tmp_path, duplicate=True), root, "hi")
    assert not (root / "hi").exists()


def test_archive_size_and_disk_bounds_before_sparse_allocation(tmp_path, monkeypatch):
    server = FakeServer()
    monkeypatch.setattr(
        setup.shutil, "disk_usage", lambda path: type("Disk", (), {"free": 1})()
    )
    target = tmp_path / "low-disk.part"
    with pytest.raises(RuntimeError, match="disk space"):
        setup.download_archive(URL, target, opener_factory=lambda: server)
    assert not target.exists()


def test_connection_deadline_is_shorter_than_stream_deadline(monkeypatch):
    phases = []

    class Socket:
        def settimeout(self, timeout):
            phases.append(("read", timeout))

    def connect(connection):
        phases.append(("connect", connection.timeout))
        connection.sock = Socket()

    monkeypatch.setattr(setup.http.client.HTTPSConnection, "connect", connect)
    connection = setup.CheckpointHTTPSConnection(
        "release-assets.githubusercontent.com", timeout=60
    )
    connection.connect()
    assert phases == [("connect", 5), ("read", 60)]
    assert connection.timeout == 60


def test_connection_restores_timeout_after_failed_connect(monkeypatch):
    def fail(connection):
        assert connection.timeout == 5
        raise TimeoutError("unresponsive CDN address")

    monkeypatch.setattr(setup.http.client.HTTPSConnection, "connect", fail)
    connection = setup.CheckpointHTTPSConnection(
        "release-assets.githubusercontent.com", timeout=60
    )
    with pytest.raises(TimeoutError):
        connection.connect()
    assert connection.timeout == 60
