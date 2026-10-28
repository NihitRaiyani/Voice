import asyncio
from uuid import uuid4

import fakeredis.aioredis
from roma.services.privacy_service import PrivacyService


class Repository:
    def __init__(self):
        self.calls = []
        self.status = "completed"

    async def plan(self, call_id):
        return {
            "call_sid": "CA_private",
            "status": self.status,
            "counts": {"transcripts": 1},
            "shared_caller_profile": True,
        }

    async def dispose(self, *args, **kwargs):
        self.calls.append("disposed")

    async def cleanup_result(self, *args, **kwargs):
        self.calls.append(kwargs["pending"])


def test_preview_never_creates_or_deletes_artifacts_and_apply_is_retryable(tmp_path):
    async def scenario():
        repo = Repository()
        raw = tmp_path / "media/2026-10-24/CA_private.s16le"
        raw.parent.mkdir(parents=True)
        raw.write_bytes(b"synthetic")
        spool = tmp_path / "spool/postcall/20261024-CA_private.json"
        spool.parent.mkdir(parents=True)
        spool.write_text("{}")
        keep = raw.with_name("CA_private_other.s16le")
        keep.write_bytes(b"keep")
        service = PrivacyService(repo, tmp_path)
        result = await service.dispose(uuid4())
        assert not result["applied"] and repo.calls == [] and raw.exists()
        assert not (tmp_path / "privacy").exists()
        result = await service.dispose(uuid4(), apply=True)
        assert result["cleanup_pending"] == ["redis"]
        assert not result["managed_cleanup_complete"]
        assert not raw.exists() and not spool.exists() and keep.exists()
        assert repo.calls == ["disposed", True]
        service.redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        await service.redis.set("call:CA_private:state", "synthetic")
        await service.redis.lpush(
            "queue:postcall", '{"call_sid":"CA_private"}', '{"call_sid":"CA_other"}'
        )
        result = await service.dispose(uuid4(), apply=True)
        assert result["managed_cleanup_complete"] and result["external_pending"]
        assert await service.redis.get("call:CA_private:state") is None
        assert await service.redis.llen("queue:postcall") == 1
        await service.redis.aclose()

    asyncio.run(scenario())


def test_symlink_escape_stays_pending_and_never_unlinks_target(tmp_path):
    async def scenario():
        target = tmp_path / "outside.wav"
        target.write_bytes(b"keep")
        root = tmp_path / "root"
        day = root / "recordings/2026-10-24"
        day.mkdir(parents=True)
        (day / "CA_private.wav").symlink_to(target)
        repo = Repository()
        service = PrivacyService(repo, root)
        result = await service.dispose(uuid4(), apply=True)
        assert "local_files" in result["cleanup_pending"] and target.read_bytes() == b"keep"

    asyncio.run(scenario())


def test_relative_runtime_root_cleanup_and_legacy_recording_reference(tmp_path, monkeypatch):
    async def scenario():
        monkeypatch.chdir(tmp_path)
        root = tmp_path / "runtime"
        recording = root / "recordings/legacy.wav"
        recording.parent.mkdir(parents=True)
        recording.write_bytes(b"synthetic")

        class LegacyRepository(Repository):
            async def plan(self, call_id):
                return {**await super().plan(call_id), "recording_refs": ["legacy.wav"]}

        result = await PrivacyService(LegacyRepository(), "runtime").dispose(
            uuid4(), apply=True
        )
        assert not recording.exists() and "local_files" not in result["cleanup_pending"]

    asyncio.run(scenario())


def test_subject_disposal_previews_all_siblings_and_retries_each_managed_cleanup(tmp_path):
    async def scenario():
        caller_id, first, second = uuid4(), uuid4(), uuid4()

        class SubjectRepository(Repository):
            async def plan_subject(self, subject_id):
                assert subject_id == caller_id
                return {"call_ids": [first, second]}

            async def plan(self, call_id):
                return {
                    **await super().plan(call_id),
                    "call_sid": "CA_first" if call_id == first else "CA_second",
                }

            async def dispose_subject(self, subject_id, *, call_ids, **kwargs):
                assert subject_id == caller_id and call_ids == [first, second]
                self.calls.append("subject_disposed")

        repo = SubjectRepository()
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        for sid in ("CA_first", "CA_second"):
            raw = tmp_path / f"media/2026-10-28/{sid}.s16le"
            raw.parent.mkdir(parents=True, exist_ok=True)
            raw.write_bytes(b"synthetic")
            await redis.set(f"call:{sid}:state", "synthetic")
        service = PrivacyService(repo, tmp_path, redis=redis)
        preview = await service.dispose_subject(caller_id)
        assert preview["counts"] == {"calls": 2, "transcripts": 2}
        assert preview["applied"] is False and repo.calls == []
        assert not (tmp_path / "privacy").exists()
        result = await service.dispose_subject(caller_id, apply=True)
        assert result["managed_cleanup_complete"] and result["external_pending"]
        assert repo.calls == ["subject_disposed", False, False]
        assert all(
            not (tmp_path / f"media/2026-10-28/{sid}.s16le").exists()
            for sid in ("CA_first", "CA_second")
        )
        assert await redis.dbsize() == 0
        await redis.aclose()

    asyncio.run(scenario())
