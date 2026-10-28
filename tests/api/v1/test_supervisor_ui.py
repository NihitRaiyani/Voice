"""The public supervisor shell contains no data; protected APIs own authorization."""

import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def client():
    from roma.api.v1.supervisor_ui import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_supervisor_shell_is_data_free_and_requires_local_scripts():
    response = client().get("/admin/supervisor")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert '<form id="login-form"' in response.text
    assert 'type="password"' in response.text
    assert "/admin/supervisor/assets/app.js" in response.text
    assert "/admin/supervisor/assets/style.css" in response.text
    assert "No caller names, transcripts or audio" in response.text
    assert "SQL snapshots refresh every 30 seconds" in response.text
    assert "background tabs may refresh later" in response.text
    assert "<script>" not in response.text and "onclick=" not in response.text
    assert "call_id" not in response.text
    csp = response.headers["content-security-policy"]
    for policy in (
        "default-src 'none'",
        "script-src 'self'",
        "style-src 'self'",
        "connect-src 'self'",
        "frame-ancestors 'none'",
        "base-uri 'none'",
    ):
        assert policy in csp
    assert "unsafe-inline" not in csp and "https:" not in csp
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"


@pytest.mark.parametrize(
    "asset,media_type", [("app.js", "text/javascript"), ("style.css", "text/css")]
)
def test_supervisor_assets_are_local_and_not_cacheable(asset, media_type):
    response = client().get("/admin/supervisor/assets/" + asset)
    assert response.status_code == 200
    assert media_type in response.headers["content-type"]
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("path", ["secrets.env", "supervisor_ui.py", "missing.js"])
def test_only_named_supervisor_assets_are_exposed(path):
    assert client().get("/admin/supervisor/assets/" + path).status_code == 404


@pytest.mark.skipif(
    shutil.which("node") is None, reason="Client runtime checks require Node.js"
)
def test_shipped_supervisor_client_session_and_rendering_behaviors():
    root = Path(__file__).resolve().parents[3]
    subprocess.run(
        [
            shutil.which("node"),
            str(Path(__file__).with_name("fixtures") / "supervisor_client.mjs"),
            str(root / "roma/api/v1/supervisor_assets/app.js"),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
