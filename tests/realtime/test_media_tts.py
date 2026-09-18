"""Tests for `TTSAudioSanitizer` — the outbound WAV-header / PCM-alignment fix.

Each test pins one behavior offline (no Sarvam socket, no credits): the header strip,
raw-PCM passthrough, odd-byte carry across chunks, carry reset at stream boundaries, and
that non-audio frames are untouched. Rationale for the fix lives in `roma.realtime.tts`.
"""

import asyncio

from pipecat.frames.frames import TextFrame, TTSAudioRawFrame
from pipecat.tests.utils import run_test
from roma.realtime.tts import TTSAudioSanitizer

_HEADER = b"RIFF" + bytes(40)


def _run(proc, frames):
    down, _ = asyncio.run(run_test(proc, frames_to_send=frames))
    return down


def _audio(down) -> bytes:
    """Concatenate the PCM the sanitizer emitted, in order."""
    return b"".join(f.audio for f in down if isinstance(f, TTSAudioRawFrame))


def _tts(pcm: bytes, ctx: str = "t") -> TTSAudioRawFrame:
    return TTSAudioRawFrame(pcm, 8000, 1, context_id=ctx)


def test_strips_leading_wav_header():
    pcm = b"\x01\x02\x03\x04"
    down = _run(TTSAudioSanitizer(), [_tts(_HEADER + pcm)])
    assert _audio(down) == pcm


def test_raw_pcm_chunk_without_header_passes_through():
    pcm = b"\xaa\xbb\xcc\xdd"
    down = _run(TTSAudioSanitizer(), [_tts(pcm)])
    assert _audio(down) == pcm


def test_odd_byte_is_carried_and_realigned_across_chunks():
    down = _run(
        TTSAudioSanitizer(),
        [_tts(_HEADER + b"\x01\x02\x03"), _tts(b"\x04\x05")],
    )
    assert _audio(down) == b"\x01\x02\x03\x04"


def test_new_wav_header_resets_stale_carry():
    down = _run(
        TTSAudioSanitizer(),
        [_tts(b"\x01\x02\x03"), _tts(_HEADER + b"\x0a\x0b\x0c\x0d")],
    )
    assert _audio(down) == b"\x01\x02" + b"\x0a\x0b\x0c\x0d"


def test_context_change_resets_stale_carry():
    down = _run(
        TTSAudioSanitizer(),
        [_tts(b"\x01\x02\x03", ctx="a"), _tts(b"\x04\x05\x06\x07", ctx="b")],
    )
    assert _audio(down) == b"\x01\x02" + b"\x04\x05\x06\x07"


def test_non_audio_frames_pass_through_untouched():
    down = _run(TTSAudioSanitizer(), [TextFrame("hello")])
    texts = [f.text for f in down if isinstance(f, TextFrame)]
    assert "hello" in texts
