"""Short section-20 lab writes using existing calls, turns and events; no migration."""

import asyncio
import math
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from roma.core.voice_errors import VoiceErrorCode, map_voice_error
from roma.domain.safety.audit import POLICY_VERSION
from roma.repositories.postgres.models import Call, CallEvent, CallTurn


class PostgresVoiceLabStore:
    def __init__(self, session_factory, *, persist_transcripts=False, timeout=3.0):
        if not math.isfinite(timeout) or not 0 < timeout <= 10:
            raise ValueError("Invalid voice persistence deadline")
        self.factory, self.persist_transcripts, self.timeout = (
            session_factory,
            persist_transcripts,
            timeout,
        )

    async def start(self, sid):
        if not isinstance(sid, str) or not sid.startswith("voice-lab-") or len(sid) > 100:
            raise ValueError("Voice lab requires an isolated call identity")
        now = datetime.now(UTC)
        async with asyncio.timeout(self.timeout):
            async with self.factory() as session, session.begin():
                session.add(
                    Call(
                        id=uuid4(),
                        provider_call_id=sid,
                        direction="inbound",
                        status="in_progress",
                        started_at=now,
                        language="hi",
                        retention_until=now + timedelta(hours=4),
                    )
                )

    async def save(self, sid, record):
        # Detach/validate bounded, PII-free timing metadata before opening a session.
        timings = dict(record["timings"])
        allowed = {
            "capture_ms",
            "vad_endpoint_ms",
            "asr_ms",
            "llm_ms",
            "routing_controller_ms",
            "safety_normalization_ms",
            "tts_cache_frames_ms",
            "safety_tts_cache_ms",
            "tts_inference_ms",
            "audio_ms",
            "playback_ms",
            "response_total_ms",
        }
        if not set(timings) <= allowed or any(
            type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 3_600_000
            for v in timings.values()
        ):
            raise ValueError("Invalid voice timings")
        attempt, turn = record["attempt"], record["turn_number"]
        if (
            type(attempt) is not int
            or not 1 <= attempt <= 1000
            or (turn is not None and (type(turn) is not int or not 1 <= turn <= 1000))
        ):
            raise ValueError("Invalid voice turn identity")
        status, stage, language = record["status"], record["stage"], record["language"]
        if status not in {"received", "ready", "played", "failed"} or stage not in {
            "open",
            "discover",
            "value",
            "structure",
            "pivot",
            "objection",
            "close",
        }:
            raise ValueError("Invalid voice turn state")
        if not isinstance(language, str) or len(language) > 32:
            raise ValueError("Invalid voice language")
        error = record["error_code"]
        if error is not None:
            try:
                error = map_voice_error(VoiceErrorCode(error)).value
            except (ValueError, TypeError):
                raise ValueError("Invalid voice error code") from None
        payload = {
            "attempt": attempt,
            "turn_number": turn,
            "status": status,
            "language": language,
            "error_code": error,
            "timings": timings,
            "source": "microphone_lab",
            "policy_version": POLICY_VERSION,
            "transcripts_retained": self.persist_transcripts,
        }
        caller, agent = record["caller"], record["agent"]
        for text in (caller, agent):
            if text is not None and (not isinstance(text, str) or len(text.encode()) > 16384):
                raise ValueError("Invalid voice transcript size")
        if self.persist_transcripts:
            payload.update(caller_transcript=caller)
        now = datetime.now(UTC)
        async with asyncio.timeout(self.timeout):
            async with self.factory() as session, session.begin():
                call = await session.scalar(
                    select(Call).where(
                        Call.provider_call_id == sid,
                        Call.deleted_at.is_(None),
                        Call.status == "in_progress",
                    )
                )
                if call is None or not sid.startswith("voice-lab-"):
                    raise ValueError("Missing lab call")
                if turn is not None:
                    values = dict(
                        call_id=call.id,
                        turn_number=turn,
                        speaker="agent",
                        transcript=agent if self.persist_transcripts else None,
                        conversation_stage=stage,
                        language=language,
                        route="microphone_lab",
                        latency_ms=round(timings.get("response_total_ms", 0)),
                        retention_until=call.retention_until,
                    )
                    await session.execute(
                        insert(CallTurn)
                        .values(
                            id=uuid5(NAMESPACE_URL, f"roma:{call.id}:turn:{turn}"), **values
                        )
                        .on_conflict_do_update(
                            index_elements=[CallTurn.call_id, CallTurn.turn_number], set_=values
                        )
                    )
                identity = f"{sid}:{attempt}:{status}"
                await session.execute(
                    insert(CallEvent)
                    .values(
                        id=uuid5(NAMESPACE_URL, identity),
                        call_id=call.id,
                        event_type="voice_lab.turn",
                        conversation_stage=stage,
                        payload=payload,
                        occurred_at=now,
                        idempotency_key=identity,
                    )
                    .on_conflict_do_update(
                        index_elements=[CallEvent.idempotency_key], set_={"payload": payload}
                    )
                )
                call.final_stage, call.language = stage, language

    async def finish(self, sid, *, failed=False):
        async with asyncio.timeout(self.timeout):
            async with self.factory() as session, session.begin():
                call = await session.scalar(select(Call).where(Call.provider_call_id == sid))
                if call is not None and sid.startswith("voice-lab-"):
                    call.status = "failed" if failed else "completed"
                    call.ended_at = datetime.now(UTC)
