import asyncio
from datetime import UTC, datetime

from roma.core.field_encryption import EncryptedText
from roma.core.recording_consent import recording_consent_valid, verified_recording_consent
from roma.repositories.postgres.models import Caller, CallTurn
from roma.workers.postcall.job import PostcallJob
from roma.workers.postcall.worker import JobResult, WorkerDeps, handle_job


def test_selected_pii_columns_share_encryption_boundary():
    assert isinstance(Caller.__table__.c.name.type, EncryptedText)
    assert isinstance(CallTurn.__table__.c.transcript.type, EncryptedText)


def test_pending_wording_and_missing_per_call_evidence_never_authorize_capture():
    async def scenario():
        assert await verified_recording_consent(None, "CA_example") is None
        assert await verified_recording_consent(lambda _: {}, "CA_example") is None

    asyncio.run(scenario())


def test_old_jobs_without_explicit_consent_are_discarded(tmp_path):
    now = datetime.now(UTC)
    raw = tmp_path / "capture.s16le"
    raw.write_bytes(b"not consented")
    job = PostcallJob(
        "CA_example", "capture.s16le", None, now.isoformat(), now.isoformat(), 1, 1, "no_lock"
    )

    class Store:
        def already_stored(self, job):
            raise AssertionError("must reject before storage")

    deps = WorkerDeps(None, Store(), tmp_path, lambda: True, 90)
    assert asyncio.run(handle_job(job, deps)) is JobResult.ACK
    assert not raw.exists()
    assert not recording_consent_valid(job, now)


def test_explicit_retention_policy_is_attached_to_real_postcall_writer():
    from datetime import timedelta
    from unittest.mock import AsyncMock

    from roma.core.privacy_settings import PrivacySettings
    from roma.services.postcall_service import PostcallService

    now = datetime.now(UTC)
    job = PostcallJob(
        "CA_retention", None, None, now.isoformat(), now.isoformat(), 0, 0, "no_lock"
    )
    store = AsyncMock()
    asyncio.run(
        PostcallService(
            store, privacy_settings=PrivacySettings(_env_file=None, privacy_retention_days=7)
        ).persist(job)
    )
    call, jobs = store.save_call_and_jobs.call_args.args
    assert call.retention_until == now + timedelta(days=7)
    assert all(row.retention_until == call.retention_until for row in jobs)


def test_consent_provider_failure_wrong_call_and_expired_evidence_fail_closed(monkeypatch):
    from datetime import timedelta

    import roma.core.recording_consent as module

    monkeypatch.setattr(module, "consent_signed_off", lambda: True)
    now = datetime.now(UTC)
    evidence = {
        "call_sid": "CA_example",
        "granted": True,
        "granted_at": now.isoformat(),
        "disclosure_version": "synthetic",
        "retention_until": (now + timedelta(days=1)).isoformat(),
    }

    async def scenario():
        assert await verified_recording_consent(lambda _: evidence, "CA_example") == evidence
        assert await verified_recording_consent(lambda _: evidence, "CA_other") is None
        assert (
            await verified_recording_consent(
                lambda _: {**evidence, "granted": False}, "CA_example"
            )
            is None
        )
        assert (
            await verified_recording_consent(
                lambda _: {**evidence, "retention_until": now.isoformat()}, "CA_example"
            )
            is None
        )

        def broken(_):
            raise RuntimeError("must not escape")

        assert await verified_recording_consent(broken, "CA_example") is None

    asyncio.run(scenario())
