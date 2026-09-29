"""Turn a completed call into one durable record and retryable post-call intents."""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

from roma.domain.persistence import CallRecord, FollowupJobRecord
from roma.domain.postcall import OUTCOME_LOCKED, CompletedCall


class PostcallService:
    def __init__(self, store) -> None:
        self._store = store

    async def persist(
        self, message: CompletedCall, recording_outcome: str = "not_requested"
    ) -> None:
        ended_at = datetime.fromisoformat(message.ended_at)
        started_at = datetime.fromisoformat(message.started_at)
        if ended_at.tzinfo is None or started_at.tzinfo is None:
            raise ValueError("post-call timestamps must include timezones")

        call = CallRecord(
            id=uuid4(),
            caller_id=None,
            provider_call_id=message.call_sid,
            direction=message.direction,
            status="completed",
            started_at=started_at,
            ended_at=ended_at,
            final_stage=message.final_stage,
            created_at=ended_at,
            updated_at=ended_at,
        )
        facts = {
            "v": 1,
            "duration_secs": message.duration_secs,
            "audio_secs": message.audio_secs,
            "turn_count": message.turn_count,
            "outcome": message.outcome,
            "final_stage": message.final_stage,
        }
        types = ["compute_statistics", "summarize_call", "update_lead"]
        if message.recording_ref is not None:
            types.insert(0, "process_recording")
        if message.outcome == OUTCOME_LOCKED:
            types.append("send_followup")
        jobs = tuple(
            FollowupJobRecord(
                id=uuid4(),
                job_type=job_type,
                payload={
                    **facts,
                    **(
                        {"locked_slot": message.locked_slot}
                        if job_type == "send_followup"
                        else {}
                    ),
                    **(
                        {"result": recording_outcome} if job_type == "process_recording" else {}
                    ),
                },
                status=(
                    "dead_letter" if recording_outcome == "missing_capture" else "succeeded"
                )
                if job_type == "process_recording"
                else "pending",
                attempts=0,
                available_at=(
                    ended_at + timedelta(hours=1) if job_type == "send_followup" else ended_at
                ),
                created_at=ended_at,
                updated_at=ended_at,
                idempotency_key=f"postcall:{message.call_sid}:{job_type}:v1",
            )
            for job_type in types
        )
        await self._store.save_call_and_jobs(call, jobs, caller_digest=message.caller_digest)
