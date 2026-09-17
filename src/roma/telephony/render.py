"""Offline Sarvam TTS: text -> 8kHz PCM16, for clips rendered outside a call.

Moved out of `scripts/make_filler_clips.py` on 2026-08-04 so the SERVER can use it too. The
web UI places calls from inside the FastAPI process, and that process cannot import from
`scripts/`; the alternative was a second rendering path for the pre-rendered opener.

A second path is exactly the wrong thing here. `VOICE`, `PACE` and `MODEL` below are what
make Roma sound like one person — a clip rendered by a different code path with a drifted
constant puts two Romas on one call, and the filler clips are spoken back-to-back with the
opener where any mismatch is audible. One definition, both callers.

This is the REST endpoint, deliberately: it renders complete clips ahead of time. The live
turn-by-turn path is the persistent Bulbul websocket in `media.py` and is unrelated.
"""

import base64

SARVAM_TTS_URL = "https://api.sarvam.ai/text-to-speech"
MODEL = "bulbul:v3"
VOICE = "ishita"
PACE = 1.05
LANGUAGE = "hi-IN"
RATE = 8000


def trim_silence(pcm: bytes, floor: int = 250, keep_tail_ms: int = 40) -> bytes:
    """Strip Bulbul's leading/trailing padding so the clip is speech, not silence.

    Rendering "Achha…" came back at 0.55-0.71s — two to three times docs/05's 200-300ms —
    because the model pads short utterances. That padding is not harmless: the filler sits
    IN FRONT of Roma's real sentence, so every millisecond of silence in it is added to the
    latency it was supposed to hide.

    A small tail is kept so the clip does not end on a hard cut, which reads as a click on
    an 8kHz line.
    """
    n = len(pcm) // 2
    samples = [int.from_bytes(pcm[i * 2 : i * 2 + 2], "little", signed=True) for i in range(n)]
    loud = [i for i, s in enumerate(samples) if abs(s) > floor]
    if not loud:
        return pcm
    start, end = loud[0], min(n, loud[-1] + int(keep_tail_ms * 8))
    return pcm[start * 2 : end * 2]


async def render(session, api_key: str, text: str) -> bytes:
    """Text -> 8kHz mono PCM16 bytes via Sarvam's REST endpoint."""
    payload = {
        "text": text,
        "target_language_code": LANGUAGE,
        "speaker": VOICE,
        "model": MODEL,
        "pace": PACE,
        "speech_sample_rate": RATE,
        "enable_preprocessing": True,
    }
    headers = {"api-subscription-key": api_key, "Content-Type": "application/json"}
    async with session.post(SARVAM_TTS_URL, json=payload, headers=headers) as resp:
        if resp.status != 200:
            raise RuntimeError(f"Sarvam {resp.status}: {await resp.text()}")
        data = await resp.json()
    if not data.get("audios"):
        raise RuntimeError("Sarvam returned no audio")
    audio = base64.b64decode(data["audios"][0])
    if len(audio) > 44 and audio.startswith(b"RIFF"):
        audio = audio[44:]
    return audio


async def render_trimmed(api_key: str, text: str) -> bytes:
    """`render` + `trim_silence` with a session of its own — the whole job, one call.

    This is what a server-side caller wants: the dialer has no aiohttp session lying around
    and no reason to learn about one.
    """
    import aiohttp

    async with aiohttp.ClientSession() as session:
        return trim_silence(await render(session, api_key, text))


__all__ = [
    "render",
    "render_trimmed",
    "trim_silence",
    "SARVAM_TTS_URL",
    "MODEL",
    "VOICE",
    "PACE",
    "LANGUAGE",
    "RATE",
]
