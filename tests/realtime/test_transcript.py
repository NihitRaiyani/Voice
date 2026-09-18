import asyncio

from pipecat.frames.frames import InterimTranscriptionFrame, TranscriptionFrame
from pipecat.tests.utils import run_test

from roma.telephony.transcript import TranscriptionLogger


def test_records_final_and_interim_and_passes_them_through():
    """Step 2 proof-of-transcripts: the logger counts interim vs final
    transcripts, remembers the last final text, and forwards every frame."""
    logger = TranscriptionLogger()

    received_down, _ = asyncio.run(
        run_test(
            logger,
            frames_to_send=[
                InterimTranscriptionFrame("namas", "u1", "t1"),
                TranscriptionFrame("namaste", "u1", "t2"),
            ],
            expected_down_frames=[InterimTranscriptionFrame, TranscriptionFrame],
        )
    )

    assert [type(f) for f in received_down] == [
        InterimTranscriptionFrame,
        TranscriptionFrame,
    ]
    assert logger.interim_count == 1
    assert logger.final_count == 1
    assert logger.finals == ["namaste"]
    assert logger.last_final == "namaste"


def test_ignores_non_transcription_frames():
    """Audio frames and other traffic are forwarded without being tallied."""
    from pipecat.frames.frames import InputAudioRawFrame

    logger = TranscriptionLogger()
    asyncio.run(
        run_test(
            logger,
            frames_to_send=[InputAudioRawFrame(b"\x00" * 320, 8000, 1)],
            expected_down_frames=[InputAudioRawFrame],
        )
    )
    assert logger.final_count == 0
    assert logger.interim_count == 0
    assert logger.finals == []
    assert logger.last_final is None
