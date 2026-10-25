"""Offline operator erasure contract; all provider effects are injected fakes."""

import asyncio
import json

import pytest
from roma.services.provider_erasure import ErasureManifest, ProviderErasure

ACCOUNT = "AC" + "1" * 32
CALL = "CA" + "2" * 32
RECORDING = "RE" + "3" * 32
TRANSCRIPTION = "TR" + "4" * 32


def manifest(**overrides):
    return ErasureManifest.parse(
        {
            "version": 1,
            "account_sid": ACCOUNT,
            "call_sid": CALL,
            "recording_sids": [RECORDING],
            "transcriptions": [{"sid": TRANSCRIPTION, "recording_sid": RECORDING}],
            **overrides,
        }
    )


def test_preview_is_pure_counts_with_no_provider_or_runtime_files(tmp_path):
    result = ProviderErasure(ACCOUNT).preview(manifest())
    assert result["counts"] == {"calls": 1, "recordings": 1, "transcriptions": 1}
    assert result["mode"] == "preview"
    assert not result["physical_erasure_complete"]
    assert result["manual_pending"]
    assert result["provider_retention_confirmation"]["anthropic"] == {
        "account_confirmed": False,
        "configured_model_and_features_confirmed": False,
        "agreement_confirmed": False,
    }
    assert list(tmp_path.iterdir()) == []
    assert all(
        sid not in json.dumps(result) for sid in (ACCOUNT, CALL, RECORDING, TRANSCRIPTION)
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"call_sid": "CA_short"},
        {"account_sid": "AC" + "z" * 32},
        {"recording_sids": [RECORDING, RECORDING]},
        {"transcriptions": [{"sid": TRANSCRIPTION, "recording_sid": "RE" + "5" * 32}]},
        {
            "transcriptions": [
                {"sid": TRANSCRIPTION, "recording_sid": RECORDING, "text": "secret"}
            ]
        },
        {"unknown": "secret"},
        {"version": True},
    ],
)
def test_manifest_rejects_unbounded_ambiguous_or_extra_content(overrides):
    with pytest.raises(ValueError):
        manifest(**overrides)


def test_account_binding_fails_even_in_preview():
    with pytest.raises(ValueError):
        ProviderErasure("AC" + "5" * 32).preview(manifest())


class FakeProvider:
    account_sid = ACCOUNT

    def __init__(self):
        from roma.core.erasure_contracts import ResourceMetadata

        self.resources = {
            CALL: ResourceMetadata(ACCOUNT, CALL, "completed"),
            RECORDING: ResourceMetadata(ACCOUNT, RECORDING, "completed", call_sid=CALL),
            TRANSCRIPTION: ResourceMetadata(
                ACCOUNT, TRANSCRIPTION, "completed", recording_sid=RECORDING
            ),
        }
        self.effects = []
        self.fetches = []
        self.fail_delete = None

    async def fetch(self, kind, sid):
        from roma.core.erasure_contracts import ProviderFailure

        self.fetches.append((kind, sid))
        if sid not in self.resources:
            raise ProviderFailure("absent")
        return self.resources[sid]

    async def delete(self, kind, sid):
        from roma.core.erasure_contracts import ProviderFailure

        self.effects.append((kind, sid))
        if self.fail_delete == sid:
            raise TimeoutError("credential transcript phone secret")
        if sid not in self.resources:
            raise ProviderFailure("absent")
        del self.resources[sid]


@pytest.mark.parametrize(
    "sid,field,value",
    [
        (CALL, "account_sid", "AC" + "5" * 32),
        (RECORDING, "call_sid", "CA" + "5" * 32),
        (TRANSCRIPTION, "recording_sid", "RE" + "5" * 32),
        (CALL, "status", "in-progress"),
        (RECORDING, "status", "processing"),
        (TRANSCRIPTION, "status", "in-progress"),
    ],
)
def test_verification_denies_ownership_or_active_state_before_any_delete(sid, field, value):
    from dataclasses import replace

    provider = FakeProvider()
    provider.resources[sid] = replace(provider.resources[sid], **{field: value})
    result = asyncio.run(ProviderErasure(ACCOUNT, provider=provider).verify(manifest()))
    assert result["outcome"] == "ownership_or_state_denied"
    assert "verified_manifest_digest" not in result and provider.effects == []


def test_explicit_verify_only_fetches_and_returns_manifest_bound_digest():
    provider = FakeProvider()
    result = asyncio.run(ProviderErasure(ACCOUNT, provider=provider).verify(manifest()))
    assert result["outcome"] == "verified_known_resources"
    assert result["verified_manifest_digest"] == manifest().digest
    assert len(provider.fetches) == 3 and provider.effects == []
    assert not result["physical_erasure_complete"]


def test_apply_requires_explicit_matching_digest_and_durable_ledger_before_tr_re_ca(tmp_path):
    async def scenario():
        provider = FakeProvider()
        service = ProviderErasure(ACCOUNT, provider=provider)
        path = tmp_path / "private" / "ledger.json"
        with pytest.raises(ValueError):
            await service.apply(
                manifest(), apply=False, verified_digest=manifest().digest, ledger=path
            )
        with pytest.raises(ValueError):
            await service.apply(manifest(), apply=True, verified_digest="bad", ledger=path)
        assert provider.fetches == [] and provider.effects == [] and not path.exists()
        original = provider.delete

        async def inspect_write(kind, sid):
            ledger = json.loads(path.read_text())
            assert ledger["manifest_digest"] == manifest().digest
            assert "attempting" in ledger["steps"].values()
            assert path.stat().st_mode & 0o777 == 0o600
            assert path.parent.stat().st_mode & 0o777 == 0o700
            await original(kind, sid)

        provider.delete = inspect_write
        result = await service.apply(
            manifest(), apply=True, verified_digest=manifest().digest, ledger=path
        )
        assert result["outcome"] == "known_resources_absent"
        assert not result["physical_erasure_complete"] and result["manual_pending"]
        assert [kind for kind, _ in provider.effects] == ["transcription", "recording", "call"]
        assert all(
            value == "deleted" for value in json.loads(path.read_text())["steps"].values()
        )
        assert all(
            sid not in path.read_text() for sid in (ACCOUNT, CALL, RECORDING, TRANSCRIPTION)
        )
        assert all(
            sid not in json.dumps(result) for sid in (ACCOUNT, CALL, RECORDING, TRANSCRIPTION)
        )

    asyncio.run(scenario())


def test_partial_timeout_is_retryable_and_previously_verified_404_is_bounded(tmp_path):
    async def scenario():
        provider = FakeProvider()
        service = ProviderErasure(ACCOUNT, provider=provider)
        path = tmp_path / "private" / "ledger.json"
        provider.fail_delete = RECORDING
        first = await service.apply(
            manifest(), apply=True, verified_digest=manifest().digest, ledger=path
        )
        assert first["outcome"] == "transient_pending"
        assert [kind for kind, _ in provider.effects] == ["transcription", "recording"]
        assert TRANSCRIPTION not in provider.resources and CALL in provider.resources
        provider.fail_delete = None
        second = await service.apply(
            manifest(), apply=True, verified_digest=manifest().digest, ledger=path
        )
        assert second["outcome"] == "known_resources_absent"
        effects = list(provider.effects)
        third = await service.apply(
            manifest(), apply=True, verified_digest=manifest().digest, ledger=path
        )
        assert third["outcome"] == "known_resources_absent" and effects == provider.effects
        assert len(provider.fetches) == 9
        assert "secret" not in json.dumps(first) + path.read_text()

    asyncio.run(scenario())


def test_initial_404_cannot_authorize_deletion_of_other_resources(tmp_path):
    async def scenario():
        provider = FakeProvider()
        del provider.resources[TRANSCRIPTION]
        service = ProviderErasure(ACCOUNT, provider=provider)
        result = await service.apply(
            manifest(),
            apply=True,
            verified_digest=manifest().digest,
            ledger=tmp_path / "private" / "ledger.json",
        )
        assert result["outcome"] == "ownership_unverified" and provider.effects == []

    asyncio.run(scenario())


def test_ledger_failure_and_wrong_account_provider_deny_before_delete(tmp_path, monkeypatch):
    from roma.services.erasure_ledger import ErasureLedger

    provider = FakeProvider()
    with pytest.raises(ValueError):
        ProviderErasure("AC" + "5" * 32, provider=provider)

    def fail_save(self):
        raise OSError("secret credential")

    monkeypatch.setattr(ErasureLedger, "save", fail_save)
    result = asyncio.run(
        ProviderErasure(ACCOUNT, provider=provider).apply(
            manifest(),
            apply=True,
            verified_digest=manifest().digest,
            ledger=tmp_path / "private" / "ledger.json",
        )
    )
    assert result["outcome"] == "ledger_or_configuration_pending" and provider.effects == []


def test_cancellation_keeps_durable_attempt_and_retry_handles_lost_response(tmp_path):
    async def scenario():
        provider = FakeProvider()
        service = ProviderErasure(ACCOUNT, provider=provider)
        path = tmp_path / "private" / "ledger.json"
        started = asyncio.Event()
        original = provider.delete

        async def cancel_after_remote_effect(kind, sid):
            await original(kind, sid)
            started.set()
            await asyncio.Future()

        provider.delete = cancel_after_remote_effect
        task = asyncio.create_task(
            service.apply(
                manifest(), apply=True, verified_digest=manifest().digest, ledger=path
            )
        )
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert "attempting" in json.loads(path.read_text())["steps"].values()
        provider.delete = original
        result = await service.apply(
            manifest(), apply=True, verified_digest=manifest().digest, ledger=path
        )
        assert result["outcome"] == "known_resources_absent"
        assert [kind for kind, _ in provider.effects] == ["transcription", "recording", "call"]

    asyncio.run(scenario())


def test_aggregate_deadline_bounds_slow_fetch_without_retries(tmp_path):
    async def scenario():
        provider = FakeProvider()
        original = provider.fetch

        async def slow_fetch(kind, sid):
            await original(kind, sid)
            await asyncio.Future()

        provider.fetch = slow_fetch
        service = ProviderErasure(
            ACCOUNT, provider=provider, request_timeout=1, total_timeout=0.01
        )
        result = await asyncio.wait_for(service.verify(manifest()), timeout=0.5)
        assert result["outcome"] == "transient_pending"
        assert len(provider.fetches) == 1 and provider.effects == []

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "status", ["authentication_manual", "provider_manual", "transient_pending"]
)
def test_provider_failures_stay_sanitized_and_stop_parent_deletion(tmp_path, status):
    from roma.core.erasure_contracts import ProviderFailure

    async def scenario():
        provider = FakeProvider()

        async def fail(kind, sid):
            provider.effects.append((kind, sid))
            raise ProviderFailure(status)

        provider.delete = fail
        path = tmp_path / "private" / "ledger.json"
        result = await ProviderErasure(ACCOUNT, provider=provider).apply(
            manifest(), apply=True, verified_digest=manifest().digest, ledger=path
        )
        assert result["outcome"] == status and len(provider.effects) == 1
        assert status in json.loads(path.read_text())["steps"].values()

    asyncio.run(scenario())


def test_independent_404_delete_is_safe_after_relationship_verification(tmp_path):
    from roma.core.erasure_contracts import ProviderFailure

    async def scenario():
        provider = FakeProvider()

        async def absent(kind, sid):
            provider.effects.append((kind, sid))
            raise ProviderFailure("absent")

        provider.delete = absent
        result = await ProviderErasure(ACCOUNT, provider=provider).apply(
            manifest(),
            apply=True,
            verified_digest=manifest().digest,
            ledger=tmp_path / "private" / "ledger.json",
        )
        assert result["outcome"] == "known_resources_absent" and len(provider.effects) == 3

    asyncio.run(scenario())


def test_invalid_ledger_symlink_permissions_or_digest_never_delete(tmp_path):
    async def scenario():
        for mode in ("symlink", "permissions", "digest"):
            root = tmp_path / mode
            root.mkdir(mode=0o700)
            path = root / "ledger.json"
            target = root / "target"
            target.write_text("untouched")
            if mode == "symlink":
                path.symlink_to(target)
            elif mode == "permissions":
                path.write_text("{}")
                path.chmod(0o644)
            else:
                path.write_text('{"manifest_digest":"other"}')
                path.chmod(0o600)
            provider = FakeProvider()
            result = await ProviderErasure(ACCOUNT, provider=provider).apply(
                manifest(), apply=True, verified_digest=manifest().digest, ledger=path
            )
            assert result["outcome"] == "ledger_or_configuration_pending"
            assert not provider.effects and not provider.fetches
            assert target.read_text() == "untouched"

    asyncio.run(scenario())


def test_same_ledger_concurrent_apply_fails_closed_before_duplicate_effects(tmp_path):
    async def scenario():
        provider = FakeProvider()
        started, release = asyncio.Event(), asyncio.Event()
        original = provider.delete

        async def block_delete(kind, sid):
            started.set()
            await release.wait()
            await original(kind, sid)

        provider.delete = block_delete
        service = ProviderErasure(ACCOUNT, provider=provider)
        path = tmp_path / "private" / "ledger.json"
        first = asyncio.create_task(
            service.apply(
                manifest(), apply=True, verified_digest=manifest().digest, ledger=path
            )
        )
        await started.wait()
        second = await service.apply(
            manifest(), apply=True, verified_digest=manifest().digest, ledger=path
        )
        assert second["outcome"] == "ledger_or_configuration_pending"
        assert len(provider.fetches) == 3
        release.set()
        assert (await first)["outcome"] == "known_resources_absent"
        assert len(provider.effects) == 3

    asyncio.run(scenario())


def test_fsync_failure_is_before_delete_and_keeps_retry_intent(tmp_path, monkeypatch):
    from roma.services import erasure_ledger

    provider = FakeProvider()
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)

    def denied(fd):
        raise OSError("secret filesystem path")

    monkeypatch.setattr(erasure_ledger.os, "fsync", denied)
    result = asyncio.run(
        ProviderErasure(ACCOUNT, provider=provider).apply(
            manifest(),
            apply=True,
            verified_digest=manifest().digest,
            ledger=directory / "receipt.json",
        )
    )
    assert result["outcome"] == "ledger_or_configuration_pending" and not provider.effects


def test_apply_reverifies_active_state_after_digest_issued_before_delete(tmp_path):
    from dataclasses import replace

    async def scenario():
        provider = FakeProvider()
        service = ProviderErasure(ACCOUNT, provider=provider)
        verified = await service.verify(manifest())
        provider.resources[CALL] = replace(provider.resources[CALL], status="in-progress")
        result = await service.apply(
            manifest(),
            apply=True,
            verified_digest=verified["verified_manifest_digest"],
            ledger=tmp_path / "private" / "receipt.json",
        )
        assert result["outcome"] == "ownership_or_state_denied" and not provider.effects

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "request_seconds,total", [(float("inf"), 1), (1, float("nan")), (0, 1), (1, 121)]
)
def test_unbounded_deadlines_are_denied(request_seconds, total):
    with pytest.raises(ValueError):
        ProviderErasure(ACCOUNT, request_timeout=request_seconds, total_timeout=total)
