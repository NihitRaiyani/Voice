"""Preview-first managed disposal, with durable tombstones and honest cleanup limits."""

import asyncio
import json
import os
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from roma.core.field_encryption import PrivacyError
from roma.core.privacy_paths import safe_call_sid, safe_runtime_path
from roma.core.private_files import open_private
from roma.repositories.redis.postcall_queue import DEAD_KEY, INFLIGHT_KEY, QUEUE_KEY


class PrivacyService:
    def __init__(self, repository, data_root, *, redis=None, clock=None):
        self.repository, self.root, self.redis = repository, Path(data_root).absolute(), redis
        self.clock = clock or (lambda: datetime.now(UTC))

    async def dispose(self, call_id, *, action="delete", apply=False, actor_id=None):
        if action not in {"delete", "anonymize"} or type(apply) is not bool:
            raise ValueError("Invalid privacy operation.")
        plan = await self.repository.plan(call_id)
        result = {
            "call_id": str(call_id),
            "action": action,
            "applied": False,
            "counts": plan["counts"],
            "shared_caller_profile": plan["shared_caller_profile"],
            "external_pending": [
                "backups_and_restore_tombstone_reapplication",
                "external_provider_and_operator_exports",
                "unmapped_lead_cache_until_ttl",
            ],
        }
        if not apply:
            return result
        if plan["status"] in {"pending", "ringing", "in_progress"}:
            raise ValueError("Active calls must end before disposal.")
        sid = safe_call_sid(plan["call_sid"])
        # Mark on disk before SQL mutation, so an in-progress conversion cannot
        # recreate files after cleanup. A failed SQL commit remains safely retryable.
        marker = safe_runtime_path(self.root, f"privacy/tombstones/{sid}.json")
        with open_private(marker, "w") as stream:
            json.dump({"v": 1, "call_id": str(call_id)}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        await self.repository.dispose(
            call_id, action=action, actor_id=actor_id, now=self.clock()
        )
        pending = []
        try:
            await asyncio.to_thread(self._remove_local, sid, plan.get("recording_refs", []))
        except (PrivacyError, OSError, ValueError):
            pending.append("local_files")
        try:
            if self.redis is None:
                raise OSError("Redis cleanup is unavailable")
            async with asyncio.timeout(5):
                await self._remove_redis(sid)
        except Exception:  # noqa: BLE001 — cleanup remains durable and explicitly retryable
            pending.append("redis")
        await self.repository.cleanup_result(call_id, pending=bool(pending), now=self.clock())
        return {
            **result,
            "applied": True,
            "cleanup_pending": pending,
            "managed_cleanup_complete": not pending,
        }

    def _remove_local(self, sid, recording_refs):
        for reference in recording_refs:
            path = safe_runtime_path(self.root, "recordings/" + reference)
            path.unlink(missing_ok=True)
            safe_runtime_path(
                self.root, "recordings/" + str(Path(reference).with_suffix(".json"))
            ).unlink(missing_ok=True)
        # Only the known runtime layout is managed; arbitrary operator exports are
        # reported above, never recursively searched/deleted by a caller identifier.
        for subdir in ("media", "recordings", "transcripts"):
            base = safe_runtime_path(self.root, subdir)
            if not base.exists():
                continue
            for day in base.iterdir():
                if day.is_symlink():
                    raise PrivacyError("Symlink runtime references are not allowed.")
                if day.is_dir():
                    for candidate in day.iterdir():
                        if candidate.name.startswith(sid + "."):
                            safe_runtime_path(
                                self.root, candidate.relative_to(self.root).as_posix()
                            ).unlink(missing_ok=True)
        spool = safe_runtime_path(self.root, "spool/postcall")
        if spool.exists():
            for candidate in spool.rglob("*"):
                if candidate.is_symlink():
                    raise PrivacyError("Symlink runtime references are not allowed.")
                if candidate.is_file() and (
                    candidate.name.endswith("-" + sid + ".json")
                    or candidate.name.endswith("-" + sid + ".json.tmp")
                ):
                    safe_runtime_path(
                        self.root, candidate.relative_to(self.root).as_posix()
                    ).unlink(missing_ok=True)

    async def _remove_redis(self, sid):
        await self.redis.delete(
            f"call:{sid}:state", f"roma:call-lease:{sha256(sid.encode()).hexdigest()}"
        )
        for key in (QUEUE_KEY, INFLIGHT_KEY, DEAD_KEY):
            # Read bounded pages; collect matches before LREM so offsets remain
            # stable during this scan. Concurrent late deliveries are tombstoned.
            matches = []
            for offset in range(0, 10000, 200):
                page = await self.redis.lrange(key, offset, offset + 199)
                for raw in page:
                    try:
                        if json.loads(raw).get("call_sid") == sid:
                            matches.append(raw)
                    except (ValueError, TypeError, AttributeError):
                        continue
                if len(page) < 200:
                    break
            else:
                raise OSError("Queue cleanup page cap reached; retry required.")
            for raw in matches:
                await self.redis.lrem(key, 0, raw)

    async def retention(self, *, apply=False, actor_id=None, limit=100):
        ids = await self.repository.expired(self.clock(), limit=limit)
        return [await self.dispose(call_id, apply=apply, actor_id=actor_id) for call_id in ids]
