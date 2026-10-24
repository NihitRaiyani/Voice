import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import SecretStr
from roma.core.privacy_settings import PrivacySettings
from roma.services.recording_service import (
    RecordingAccess,
    RecordingService,
    RecordingUnavailable,
)


def test_recording_capability_is_expiring_user_bound_and_audit_precedes_output(tmp_path):
    async def scenario():
        now = datetime(2026, 1, 1, tzinfo=UTC)
        recording_id, call_id, user_id = uuid4(), uuid4(), uuid4()
        root = tmp_path / "recordings"
        root.mkdir()
        (root / "sample.wav").write_bytes(b"RIFFsynthetic")

        class Repository:
            fail_audit = False

            def __init__(self):
                self.audit = []

            async def recording(self, value):
                return RecordingAccess(
                    recording_id,
                    call_id,
                    "sample.wav",
                    "audio/wav",
                    now + timedelta(days=1),
                    now,
                    "available",
                )

            async def record_access(self, row, actor_id, action, **context):
                if self.fail_audit:
                    raise RecordingUnavailable()
                self.audit.append(action)

        repo = Repository()
        service = RecordingService(
            repo,
            root,
            PrivacySettings(_env_file=None, recording_url_signing_key=SecretStr("22" * 32)),
            clock=lambda: now,
        )
        issued = await service.issue(recording_id, user_id)
        token = issued["url"].split("token=")[1]
        assert await service.play(recording_id, user_id, token) == (
            b"RIFFsynthetic",
            "audio/wav",
        )
        assert repo.audit == ["recording.link_issued", "recording.playback"]
        with pytest.raises(RecordingUnavailable):
            await service.play(recording_id, uuid4(), token)
        repo.fail_audit = True
        with pytest.raises(RecordingUnavailable):
            await service.issue(recording_id, user_id)
        with pytest.raises(RecordingUnavailable):
            await service.play(recording_id, user_id, token)
        service.clock = lambda: now + timedelta(minutes=10)
        with pytest.raises(RecordingUnavailable):
            await service.play(recording_id, user_id, token)

    asyncio.run(scenario())
