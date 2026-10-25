"""Acceptance CLI lifecycle checks use scripted stores, never devices or providers."""

import asyncio
import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from roma.core.voice_errors import VoiceFailure
from roma.domain.conversation.state import CallState


def cli():
    spec = importlib.util.spec_from_file_location(
        "voice_acceptance_cli",
        Path(__file__).resolve().parents[2] / "scripts/voice_prototype.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_resume_keeps_saved_identity_deadline_and_attempt_number():
    async def run():
        module = cli()
        now = datetime.now(UTC)
        sid = "voice-lab-" + "a" * 32
        state = CallState(
            call_sid=sid, stage="discover", turn_count=3, lead_name="Synthetic learner"
        )
        call = SimpleNamespace(
            id="opaque",
            status="in_progress",
            deleted_at=None,
            ended_at=None,
            retention_until=now + timedelta(hours=1),
            started_at=now - timedelta(seconds=70),
        )

        class Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def scalar(self, query):
                if "privacy_tombstones" in str(query):
                    return None
                return call if "calls" in str(query) else 6

        class Live:
            async def load(self, requested):
                assert requested == sid
                return state

        restored, started_at, attempts = await module.resume_lab_call(
            lambda: Session(), Live(), sid, now=now
        )
        assert restored is state and restored.call_sid == sid
        assert restored.turn_count == 3 and restored.lead_name == "Synthetic learner"
        assert (now - started_at).total_seconds() == 70
        assert attempts == 6

    asyncio.run(run())


@pytest.mark.parametrize(
    "change",
    ["completed", "failed", "ended", "deleted", "expired", "deadline", "missing_state"],
)
def test_resume_never_reopens_terminal_or_expired_calls(change):
    async def run():
        module, now = cli(), datetime.now(UTC)
        sid = "voice-lab-" + "b" * 32
        state = CallState(call_sid=sid)
        call = SimpleNamespace(
            id="opaque",
            status="in_progress",
            deleted_at=None,
            ended_at=None,
            retention_until=now + timedelta(hours=1),
            started_at=now - timedelta(seconds=20),
        )
        if change in {"completed", "failed"}:
            call.status = change
        if change == "ended":
            call.ended_at = now
        if change == "deleted":
            call.deleted_at = now
        if change == "expired":
            call.retention_until = now
        if change == "deadline":
            call.started_at = now - timedelta(seconds=300)

        class Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def scalar(self, query):
                return None if "privacy_tombstones" in str(query) else call

        class Live:
            async def load(self, requested):
                return None if change == "missing_state" else state

        with pytest.raises(VoiceFailure):
            await module.resume_lab_call(lambda: Session(), Live(), sid, now=now)
        assert call.status == (change if change in {"completed", "failed"} else "in_progress")

    asyncio.run(run())


def test_cli_resume_pause_options_are_explicit_and_capture_cue_is_flushed(capsys, monkeypatch):
    module = cli()
    args = module.parser().parse_args(
        ["--mode", "local", "--resume-call", "voice-lab-" + "c" * 32, "--pause-after-turn", "2"]
    )
    assert args.pause_after_turn == 2 and args.resume_call.startswith("voice-lab-")
    writes = []
    monkeypatch.setattr(
        "builtins.print", lambda *values, **kwargs: writes.append((values, kwargs))
    )
    module.capture_started(args.resume_call, 7)
    assert writes[0][1]["flush"] is True
    import json

    cue = json.loads(writes[0][0][0])
    assert cue == {"event": "CAPTURE_STARTED", "call_sid": args.resume_call, "attempt": 7}


def test_pause_checkpoints_then_records_nonterminal_status():
    async def run():
        module, events = cli(), []
        state = CallState(call_sid="voice-lab-" + "d" * 32, turn_count=3)

        class Scope:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

        class Session(Scope):
            def begin(self):
                return Scope()

            async def execute(self, query):
                events.append("paused_event")

        class Live:
            async def checkpoint(self, current, milestone):
                assert current is state
                events.append("checkpoint")

        await module.pause_lab_call(lambda: Session(), Live(), state, 4)
        assert events == ["checkpoint", "paused_event"]

    asyncio.run(run())


def test_suspension_after_capture_never_enters_controller_or_paid_provider():
    async def run():
        module = cli()
        started = datetime.now(UTC)
        wall = [started]
        deadline = module.LabDeadline(started, wall_clock=lambda: wall[0], clock=lambda: 100.0)
        calls = []

        class Device:
            async def listen(self, speech, *, on_capture_started, **kwargs):
                on_capture_started()
                wall[0] += timedelta(hours=6)
                return SimpleNamespace(text="Synthetic reply"), {}

        class Voice:
            state = SimpleNamespace(call_sid="voice-lab-" + "e" * 32)
            attempt = 0

            async def respond(self, *args):
                calls.append("controller")

        with pytest.raises(VoiceFailure):
            await module.capture_turn(
                Device(), object(), Voice(), SimpleNamespace(max_utterance_ms=30000), deadline
            )
        assert calls == []

    asyncio.run(run())


def test_resume_tombstone_is_denied_before_checkpoint_or_cache_load():
    async def run():
        module, queries = cli(), []

        class Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def scalar(self, query):
                queries.append(str(query))
                return "opaque-tombstone"

        class Live:
            async def load(self, sid):
                pytest.fail("Privacy denial must precede checkpoint/cache publication")

        with pytest.raises(VoiceFailure):
            await module.resume_lab_call(lambda: Session(), Live(), "voice-lab-" + "f" * 32)
        assert "privacy_tombstones" in queries[0]

    asyncio.run(run())


def test_local_privacy_marker_prevents_any_resume_database_or_cache_access(tmp_path):
    async def run():
        module = cli()
        sid = "voice-lab-" + "0" * 32
        marker = tmp_path / "privacy" / "tombstones" / (sid + ".json")
        marker.parent.mkdir(parents=True)
        marker.write_text("{}")

        def factory():
            pytest.fail("Local privacy marker must deny before SQL or cache access")

        with pytest.raises(VoiceFailure):
            await module.resume_lab_call(factory, object(), sid, data_root=tmp_path)

    asyncio.run(run())


def test_wall_rollback_does_not_extend_the_monotonic_session_limit():
    module = cli()
    started = datetime.now(UTC)
    wall, mono = [started + timedelta(seconds=70)], [100.0]
    deadline = module.LabDeadline(started, wall_clock=lambda: wall[0], clock=lambda: mono[0])
    assert deadline.remaining() == 230
    wall[0], mono[0] = started + timedelta(seconds=40), 110.0
    assert deadline.remaining() == 220
    mono[0] += 221
    assert deadline.remaining() == 0


def test_suspension_before_capture_open_cue_never_announces_or_responds(capsys):
    async def run():
        module, started = cli(), datetime.now(UTC)
        wall = [started]
        deadline = module.LabDeadline(started, wall_clock=lambda: wall[0], clock=lambda: 100.0)

        class Device:
            async def listen(self, speech, *, on_capture_started, **kwargs):
                wall[0] += timedelta(hours=6)
                on_capture_started()
                pytest.fail("Expired capture must close before returning a transcript")

        voice = SimpleNamespace(
            state=SimpleNamespace(call_sid="voice-lab-" + "1" * 32), attempt=0
        )
        with pytest.raises(VoiceFailure):
            await module.capture_turn(
                Device(), object(), voice, SimpleNamespace(max_utterance_ms=30000), deadline
            )

    asyncio.run(run())
    assert "CAPTURE_STARTED" not in capsys.readouterr().out


def test_suspension_during_synthesis_cannot_open_a_speaker_or_play_late_audio():
    from roma.providers.audio.local_device import LocalAudioDevice
    from tests.services.test_voice_prototype import setup, transcript

    async def run():
        module, started = cli(), datetime.now(UTC)
        wall = [started]
        deadline = module.LabDeadline(started, wall_clock=lambda: wall[0], clock=lambda: 100.0)

        def output(**kwargs):
            pytest.fail("Expired response must never open the physical output stream")

        speaker = LocalAudioDevice(
            backend=SimpleNamespace(RawOutputStream=output),
            check_active=deadline.require_remaining,
        )
        voice = setup(speaker=speaker)
        original = voice.synthesis.synthesize

        async def delayed(*args, **kwargs):
            result = await original(*args, **kwargs)
            wall[0] += timedelta(hours=6)
            return result

        voice.synthesis.synthesize = delayed
        with pytest.raises(VoiceFailure):
            await voice.respond(transcript())
        assert voice.store.records[-1][1]["status"] == "failed"
        assert voice.store.records[-1][1]["error_code"] == "CAPTURE"

    asyncio.run(run())


def test_capture_turn_stamps_one_fresh_epoch_before_asr_and_preserves_it_for_reply():
    from roma.core.turn_epoch import TurnEpoch
    from roma.domain.audio.turn_policy import TurnPolicy
    from roma.services.speech_input import SpeechTranscript

    async def run():
        module = cli()
        guard = TurnEpoch()
        voice = SimpleNamespace(state=CallState(call_sid="voice-lab-" + "f" * 32), attempt=0, epochs=guard)
        seen = []
        class Device:
            async def listen(self, speech, *, epoch=None, **kwargs):
                assert epoch == 1 and guard.current == 1
                seen.append(epoch)
                return SpeechTranscript("hello", "en", 100, "silence", 0, turn_epoch=epoch), {}
        async def respond(transcript, timings):
            assert transcript.turn_epoch == guard.current == 1
            seen.append(transcript.turn_epoch)
            return "played"
        voice.respond = respond
        result = await module.capture_turn(Device(), object(), voice, TurnPolicy(), module.LabDeadline(datetime.now(UTC)))
        assert result == "played" and seen == [1, 1]
    asyncio.run(run())


def test_capture_failure_invalidates_epoch_and_clears_before_propagating():
    from roma.core.turn_epoch import TurnEpoch
    from roma.domain.audio.turn_policy import TurnPolicy

    async def run():
        module, events = cli(), []
        guard = TurnEpoch()
        voice = SimpleNamespace(state=CallState(call_sid="voice-lab-" + "a" * 32), attempt=0, epochs=guard)
        class Device:
            async def listen(self, *args, **kwargs):
                raise TimeoutError()
            async def clear(self):
                events.append(guard.current)
        with pytest.raises(VoiceFailure):
            await module.capture_turn(Device(), object(), voice, TurnPolicy(), module.LabDeadline(datetime.now(UTC)))
        assert guard.current == 2 and events == [2]
    asyncio.run(run())
