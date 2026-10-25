"""Operator commands stay offline unless verify/apply is explicitly selected."""

import asyncio
import json

from scripts import provider_erasure

ACCOUNT = "AC" + "1" * 32
CALL = "CA" + "2" * 32


def write_manifest(tmp_path, content=None):
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps(
            content
            or {
                "version": 1,
                "account_sid": ACCOUNT,
                "call_sid": CALL,
                "recording_sids": [],
                "transcriptions": [],
            }
        )
    )
    path.chmod(0o600)
    return path


def test_default_preview_builds_no_network_client_and_ignores_dial_credentials(
    tmp_path, monkeypatch, capsys
):
    path = write_manifest(tmp_path)
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "dial-account-secret")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "dial-token-secret")

    def deny_client(*args, **kwargs):
        raise AssertionError("Preview must never initialize a provider")

    monkeypatch.setattr(provider_erasure, "build_provider", deny_client)
    assert provider_erasure.main(["--manifest", str(path), "--account-sid", ACCOUNT]) == 0
    output = capsys.readouterr().out
    assert json.loads(output)["mode"] == "preview"
    assert "dial-" not in output and CALL not in output and ACCOUNT not in output
    assert sorted(item.name for item in tmp_path.iterdir()) == ["manifest.json"]


def test_explicit_verify_requires_separate_credential_names_without_dial_fallback(
    tmp_path, monkeypatch, capsys
):
    path = write_manifest(tmp_path)
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", ACCOUNT)
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "secret-token")
    monkeypatch.delenv("ROMA_ERASURE_TWILIO_API_KEY_SID", raising=False)
    monkeypatch.delenv("ROMA_ERASURE_TWILIO_API_KEY_SECRET", raising=False)
    assert (
        provider_erasure.main(["--manifest", str(path), "--account-sid", ACCOUNT, "--verify"])
        == 1
    )
    output = capsys.readouterr()
    assert "secret" not in output.out + output.err
    assert "authentication_manual" in output.out


def test_bad_apply_digest_fails_before_even_building_client(tmp_path, monkeypatch, capsys):
    path = write_manifest(tmp_path)
    built = []
    monkeypatch.setattr(provider_erasure, "build_provider", lambda *args: built.append(True))
    assert (
        provider_erasure.main(
            [
                "--manifest",
                str(path),
                "--account-sid",
                ACCOUNT,
                "--apply",
                "--verified-digest",
                "0" * 64,
                "--ledger",
                str(tmp_path / "private" / "receipt.json"),
            ]
        )
        == 1
    )
    assert not built and "failed" in capsys.readouterr().err


def test_manifest_unknown_private_content_and_duplicate_keys_are_sanitized(tmp_path, capsys):
    path = write_manifest(tmp_path, {"transcript": "SECRET PRIVATE TEXT +919999999999"})
    assert provider_erasure.main(["--manifest", str(path), "--account-sid", ACCOUNT]) == 1
    output = capsys.readouterr()
    assert "SECRET" not in output.out + output.err and "9999999999" not in output.err
    path.write_text('{"version":1,"version":1}')
    assert provider_erasure.main(["--manifest", str(path), "--account-sid", ACCOUNT]) == 1


def test_verify_closes_injected_provider_and_apply_routes_only_explicit_target(
    tmp_path, monkeypatch
):
    path = write_manifest(tmp_path)
    closed, fetched, deleted = [], [], []

    class Provider:
        account_sid = ACCOUNT

        async def fetch(self, kind, sid):
            from roma.core.erasure_contracts import ResourceMetadata

            fetched.append((kind, sid))
            return ResourceMetadata(ACCOUNT, CALL, "completed")

        async def delete(self, kind, sid):
            deleted.append((kind, sid))

        async def aclose(self):
            closed.append(True)

    monkeypatch.setattr(provider_erasure, "build_provider", lambda *args: Provider())
    args = provider_erasure.parse_args(
        ["--manifest", str(path), "--account-sid", ACCOUNT, "--verify"]
    )
    verified = asyncio.run(provider_erasure.run(args))
    assert (
        verified["outcome"] == "verified_known_resources" and not deleted and closed == [True]
    )
    args = provider_erasure.parse_args(
        [
            "--manifest",
            str(path),
            "--account-sid",
            ACCOUNT,
            "--apply",
            "--verified-digest",
            verified["verified_manifest_digest"],
            "--ledger",
            str(tmp_path / "private" / "receipt.json"),
        ]
    )
    result = asyncio.run(provider_erasure.run(args))
    assert result["outcome"] == "known_resources_absent"
    assert fetched == [("call", CALL)] * 2 and deleted == [("call", CALL)]
    assert closed == [True, True] and result["manual_pending"]
