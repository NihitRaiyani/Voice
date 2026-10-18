"""Synthetic section 15–17 exercise; imports do not read .env or load weights."""

from dataclasses import asdict

from roma.domain.audio.endpointing import FRAME_BYTES
from roma.domain.conversation.language import route_language
from roma.domain.conversation.state import CallState
from roma.providers.ai.contracts import STTResult
from roma.services.speech_input import SpeechInputService, SpeechTranscript


class ScriptedProbability:
    """Deterministic probability stand-in, not Silero quality evidence."""

    def probability(self, pcm: bytes) -> float:
        return 0.9 if pcm[0] else 0.0

    def reset(self) -> None:
        pass


class ScriptedSTT:
    def __init__(self, transcripts: tuple[str, ...]) -> None:
        self._transcripts = iter(transcripts)
        self.calls = 0

    async def transcribe(self, request) -> STTResult:
        self.calls += 1
        return STTResult(next(self._transcripts), request.language_hint)


async def report() -> dict:
    state = CallState(call_sid="synthetic-speech")
    stt = ScriptedSTT(
        (
            "મને SEO શીખવું છે",
            "મને Google Ads શીખવું છે",
            "I want course details",
            "Please explain the class schedule",
        )
    )
    service = SpeechInputService(stt, ScriptedProbability(), state)
    cases = []
    for name in (
        "short_ack",
        "speech_returns_before_limit",
        "english_first_evidence",
        "english_sustained",
    ):
        results: list[SpeechTranscript] = []
        # One 64 ms acknowledgement, or two speech runs separated by only 320ms.
        probabilities = (
            [1] * 2 + [0] * 16
            if name != "speech_returns_before_limit"
            else [1] * 4 + [0] * 10 + [1] * 4 + [0] * 16
        )
        for speaking in probabilities:
            pcm = bytes([speaking, 0]) * (FRAME_BYTES // 2)
            results.extend(await service.feed(pcm))
        for result in results:
            route_language(
                state.language_router, result.text
            )  # Lab accepts this final only once.
            row = asdict(result)
            # Timing of a scripted fake is not an ASR latency measurement.
            row.pop("asr_ms")
            cases.append({"case": name, **row, "route": state.language_router.current.value})
    restored = CallState.from_dict(state.to_dict())
    return {
        "schema": "sections15-17-speech-v1",
        "synthetic_only": True,
        "model_downloads": False,
        "native_asr_quality_measured": False,
        "asr_calls": stt.calls,
        "cases": cases,
        "restore_matches": restored.language_router == state.language_router,
        "reply_policy": "Hindi-base Hinglish",
    }
