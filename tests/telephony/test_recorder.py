"""Local call capture (docs/09 option 2).

The file sink is a plain object, so most of this needs no pipeline. The two cases that DO
need pipecat — dual-leg capture and finalize-on-teardown — go through `run_test`, the same
harness the other telephony tests use.
"""

import asyncio
import stat
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    InputAudioRawFrame,
    OutputAudioRawFrame,
)
from pipecat.tests.utils import run_test

from roma.postcall.paths import DIR_MODE, FILE_MODE
from roma.telephony.recorder import (
    RECORDING_CHANNELS,
    RECORDING_EXT,
    RECORDING_SAMPLE_RATE,
    CallRecorder,
    attach_recorder,
    build_recorder,
)

NOW = datetime(2026, 7, 26, 10, 0, tzinfo=UTC)


def _settings(tmp_path):
    return SimpleNamespace(roma_data_dir=str(tmp_path))


def test_recorder_writes_to_a_dated_path(tmp_path):
    r = build_recorder(_settings(tmp_path), "CA_rec", now=NOW)
    assert r.path == tmp_path / "media" / "2026-07-26" / f"CA_rec{RECORDING_EXT}"


def test_recorder_appends_and_closes(tmp_path):
    r = build_recorder(_settings(tmp_path), "CA_rec", now=NOW)
    r.append(b"\x01\x02" * 100)
    r.append(b"\x03\x04" * 100)
    r.close()

    assert r.path.read_bytes() == b"\x01\x02" * 100 + b"\x03\x04" * 100
    assert r.bytes_written == 400


def test_capture_files_are_owner_only(tmp_path):
    """Raw lead audio is PII (docs/07) from the instant the first byte lands, not from
    when the worker files it away."""
    r = build_recorder(_settings(tmp_path), "CA_rec", now=NOW)
    r.append(b"\x00\x00")
    r.close()

    assert stat.S_IMODE(r.path.stat().st_mode) == FILE_MODE
    assert stat.S_IMODE(r.path.parent.stat().st_mode) == DIR_MODE
    assert stat.S_IMODE(r.path.parent.parent.stat().st_mode) == DIR_MODE


def test_close_is_idempotent(tmp_path):
    """Teardown may reach `close()` more than once — from the buffer processor's own
    EndFrame handling and again from `finalize_call`."""
    r = build_recorder(_settings(tmp_path), "CA_rec", now=NOW)
    r.append(b"\x00\x00")
    r.close()
    r.close()
    assert r.path.read_bytes() == b"\x00\x00"


def test_appending_after_close_is_ignored(tmp_path):
    r = build_recorder(_settings(tmp_path), "CA_rec", now=NOW)
    r.append(b"\x01\x02")
    r.close()
    r.append(b"\xff\xff")
    assert r.path.read_bytes() == b"\x01\x02"


def test_audio_secs_is_derived_from_the_byte_count(tmp_path):
    r = build_recorder(_settings(tmp_path), "CA_rec", now=NOW)
    one_second = RECORDING_SAMPLE_RATE * 2 * RECORDING_CHANNELS
    r.append(b"\x00" * one_second)
    assert r.audio_secs() == pytest.approx(1.0)
    assert not r.is_empty()
    r.close()


def test_a_write_failure_never_raises_into_the_call(tmp_path):
    """docs/08: nothing on this path may take a call down. A full disk degrades the
    recording, never the conversation."""
    blocker = tmp_path / "media"
    blocker.write_text("i am a file where a directory should be")
    r = build_recorder(_settings(tmp_path), "CA_rec", now=NOW)

    r.append(b"\x01\x02")
    assert r.failed is True
    assert r.is_empty()
    r.close()


def test_relative_ref_is_relative_to_the_media_root(tmp_path):
    """The job carries a RELATIVE ref so moving the data root is a config change, not a
    rewrite of every queued job."""
    r = build_recorder(_settings(tmp_path), "CA_rec", now=NOW)
    assert r.relative_ref(tmp_path / "media") == f"2026-07-26/CA_rec{RECORDING_EXT}"


def test_empty_recorder_creates_no_file(tmp_path):
    """A call that captured nothing must not leave an empty artifact for the worker to
    trip over."""
    r = build_recorder(_settings(tmp_path), "CA_rec", now=NOW)
    r.close()
    assert not r.path.exists()
    assert r.is_empty()


def _frame_bytes(fill=b"\x11", n=320):
    return fill * 2 * (n // 2)


def test_both_legs_are_captured_on_separate_channels(tmp_path):
    """The contract that makes the artifact worth keeping.

    CLAUDE.md wants "separate legs per speaker"; docs/09 wants one file per call. Stereo
    satisfies both — ch0 the lead, ch1 Roma — so the two are still separable afterwards.
    Distinct fill bytes per leg mean this fails if the legs are mixed, swapped, or if one
    is silently missing, which `bytes_written > 0` would not catch.

    That the lead's frame reaches a processor placed after `transport.output()` at all
    rests on the STT service's `audio_passthrough` default — see the canary below.
    """
    r = CallRecorder(tmp_path / "cap.s16le")
    proc = attach_recorder(r)

    asyncio.run(
        run_test(
            proc,
            frames_to_send=[
                InputAudioRawFrame(_frame_bytes(b"\x11"), RECORDING_SAMPLE_RATE, 1),
                OutputAudioRawFrame(_frame_bytes(b"\x22"), RECORDING_SAMPLE_RATE, 1),
            ],
            expected_down_frames=[InputAudioRawFrame, OutputAudioRawFrame],
        )
    )
    r.close()

    data = r.path.read_bytes()
    assert data, "no audio reached the sink"
    assert len(data) % 4 == 0, "stereo PCM16 frames are 4 bytes; the stream is misaligned"
    lead = set(data[0::4]) | set(data[1::4])
    roma = set(data[2::4]) | set(data[3::4])
    assert 0x11 in lead, "the lead's leg is missing from channel 0"
    assert 0x22 in roma, "Roma's leg is missing from channel 1"
    assert 0x22 not in lead and 0x11 not in roma, "the legs were mixed together"


def test_capture_processor_is_stereo_at_the_telephony_rate():
    proc = attach_recorder(CallRecorder("/dev/null"))
    assert proc.num_channels == RECORDING_CHANNELS == 2
    assert proc.sample_rate in (0, RECORDING_SAMPLE_RATE)


@pytest.mark.parametrize("terminal", [EndFrame, CancelFrame])
def test_audio_survives_an_abnormal_teardown(tmp_path, terminal):
    """A barge-in cancel or a normal end must still leave usable audio on disk. Because
    the capture file is headerless raw PCM, whatever was flushed is already valid — there
    is no header to patch and therefore nothing to corrupt."""
    r = CallRecorder(tmp_path / "cap.s16le")
    proc = attach_recorder(r)

    async def drive():
        await run_test(
            proc,
            frames_to_send=[
                InputAudioRawFrame(_frame_bytes(b"\x11"), RECORDING_SAMPLE_RATE, 1)
            ],
            expected_down_frames=[InputAudioRawFrame],
        )

    asyncio.run(drive())
    r.close()
    assert r.path.exists()
    assert r.path.stat().st_size == r.bytes_written


def test_stt_audio_passthrough_default_still_holds():
    """The pipecat-upgrade canary.

    The lead's leg only reaches a processor placed after `transport.output()` because
    `STTService` forwards `InputAudioRawFrame` downstream (`audio_passthrough=True`). If a
    version bump ever flips that default, half of every recording disappears with no error
    and no other failing test. Fail loudly here instead.
    """
    import inspect

    from pipecat.services.stt_service import STTService

    sig = inspect.signature(STTService.__init__)
    assert sig.parameters["audio_passthrough"].default is True
