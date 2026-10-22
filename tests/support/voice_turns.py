"""Shared synthetic voice harness: real orchestration, no hardware/models/network."""

from array import array
from collections import deque
from unittest.mock import patch

from roma.domain.conversation.language import Language
from roma.domain.conversation.state import CallState
from roma.eval.tts import SyntheticPCMProvider
from roma.providers.ai.contracts import STTResult
from roma.providers.ai.local_routing import LocalLanguageSTT
from roma.providers.ai.mocks import MockLLMProvider
from roma.providers.audio.local_device import LocalAudioDevice
from roma.services.safety_service import SafetyService
from roma.services.speech_input import SpeechInputService
from roma.services.text_conversation_service import TextConversationService
from roma.services.tts_service import SpeechSynthesisService
from roma.services.voice_prototype import InMemoryVoiceTurnStore, VoicePrototype


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, milliseconds):
        self.now += milliseconds / 1000


class Probability:
    def probability(self, pcm):
        return 0.95 if any(pcm) else 0.0

    def reset(self):
        pass


class FakeASR:
    def __init__(self, scripts, clock, *, structured=False, policy=None, english_probability=0.99):
        self.structured, self.policy, self.english_probability = structured, policy, english_probability
        self.single_calls, self.batch_calls = [], []
        self.batch_rows = None
        self.scripts = deque(scripts)
        self.requests = []
        self.clock = clock

    async def transcribe(self, request):
        self.requests.append(request)
        if self.structured:
            from roma.providers.ai.mlx_batch import choose_chunk_language, turn_chunks
            route = request.metadata.get("local_language_route", request.language_hint)
            parts = turn_chunks(request.audio, Probability(), route, self.policy)
            base = request.language_hint
            if len(parts) == 2:
                self.batch_calls.append(parts)
                self.clock.advance(10)
                if self.batch_rows is not None:
                    from roma.providers.ai.mlx_batch import validated_batch_text
                    text = validated_batch_text(self.batch_rows)
                    return STTResult(text, base, error_code="ASR-BATCH-REJECTED" if not text else None)
                english = choose_chunk_language({"en": self.english_probability, "gu": 0.1, "hi": 0.1}, base)
                return STTResult("મને અભ્યાસ વિશે જાણવું છે " + ("I would like course information" if english == "en" else "મને વધુ માહિતી જોઈએ છે"), base)
            self.single_calls.append(request.audio)
        self.clock.advance(5)
        return STTResult(self.scripts.popleft() if self.scripts else "", request.language_hint)


class FakeAudio:
    def __init__(self, clock):
        self.clock = clock
        self.pcm = b""
        self.offset = 0
        self.input_open = False
        self.closed_before_playback = False
        self.first_write_at = None

    def RawInputStream(self, **kwargs):
        owner = self

        class Input:
            def start(self):
                owner.input_open = True

            def read(self, count):
                block = owner.pcm[owner.offset:owner.offset + count * 2].ljust(count * 2, b"\0")
                owner.offset += count * 2
                owner.clock.advance(count / 16)
                return block, False

            def abort(self):
                owner.input_open = False

            def close(self):
                owner.input_open = False

        return Input()

    def RawOutputStream(self, **kwargs):
        owner = self

        class Output:
            def start(self):
                assert not owner.input_open
                owner.closed_before_playback = True

            def write(self, pcm):
                if owner.first_write_at is None:
                    owner.first_write_at = owner.clock()
                owner.clock.advance(1)
                return False

            def stop(self):
                pass

            def abort(self):
                pass

            def close(self):
                pass

        return Output()


class VoiceHarness:
    def __init__(self, *, scripts=(), route=Language.GUJARATI, policy_values=None, structured_decode=False, english_probability=0.99):
        from roma.domain.audio.turn_policy import TurnPolicy

        self.policy = TurnPolicy(**(policy_values or {}))
        self.clock = FakeClock()
        self.backend = FakeAudio(self.clock)
        self.asr = FakeASR(scripts, self.clock, structured=structured_decode, policy=self.policy, english_probability=english_probability)
        self.state = CallState(call_sid="voice-lab-harness", stage="value")
        self.state.language_router.current = route
        self.speech = SpeechInputService(LocalLanguageSTT(self.asr), Probability(), self.state, policy=self.policy)
        self.device = LocalAudioDevice(backend=self.backend, policy=self.policy)
        safety = SafetyService()
        self.store = InMemoryVoiceTurnStore()
        self.voice = VoicePrototype(
            self.state, TextConversationService(MockLLMProvider(response="नमस्ते।"), safety=safety),
            SpeechSynthesisService(SyntheticPCMProvider(), safety, model_version="synthetic-v1"),
            self.device, self.store, clock=self.clock,
        )
        self.last_transcript = None
        self.last_timings = None

    @staticmethod
    def audio(*segments):
        return b"".join(array("h", [marker]) .tobytes() * int(milliseconds * 16)
                        for marker, milliseconds in segments)

    async def capture(self, pcm, *, max_seconds=30):
        self.backend.pcm, self.backend.offset = pcm, 0
        with patch("roma.providers.audio.local_device.perf_counter", self.clock), patch(
            "roma.services.speech_input.perf_counter", self.clock
        ):
            self.last_transcript, self.last_timings = await self.device.listen(self.speech, max_seconds=max_seconds)
        return self.last_transcript

    async def run_audio(self, pcm, *, max_seconds=30):
        transcript = await self.capture(pcm, max_seconds=max_seconds)
        assert transcript is not None
        with patch("roma.providers.audio.local_device.perf_counter", self.clock), patch(
            "roma.services.text_conversation_service.perf_counter", self.clock
        ), patch("roma.services.tts_service.perf_counter", self.clock):
            return await self.voice.respond(transcript, self.last_timings)


    async def drain_audio(self, pcm):
        results = []
        while any(pcm):
            results.append(await self.run_audio(pcm))
            pcm = self.backend.pcm[self.backend.offset:]
        return results
