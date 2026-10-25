"""Shared synthetic voice harness: real orchestration, no hardware/models/network."""

from array import array
from collections import deque
from types import SimpleNamespace
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
        self.now = round(self.now + milliseconds / 1000, 9)


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
        self.software_muted_before_playback = False
        self.input_created = self.input_starts = self.input_closes = 0
        self.callback = self.capture_mode = None
        self.first_write_at = None

    def RawInputStream(self, **kwargs):
        owner = self
        self.input_created += 1
        self.callback = kwargs["callback"]

        class Input:
            def start(self):
                owner.input_open = True
                owner.input_starts += 1

            def abort(self):
                owner.input_open = False

            def close(self):
                owner.input_open = False
                owner.input_closes += 1

        return Input()

    def emit_next(self):
        """Advance one callback frame in lockstep with the fake VAD consumer.

        Ring pressure/concurrent producers have separate tests; this clock keeps
        the continuation matrix deterministic without sleeping for real audio.
        """
        assert self.input_open and self.capture_mode() == "ACTIVE"
        block = self.pcm[self.offset:self.offset + 640].ljust(640, b"\0")
        self.offset += 640
        self.clock.advance(20)
        self.callback(block, 320, SimpleNamespace(), SimpleNamespace(input_overflow=False))

    def RawOutputStream(self, **kwargs):
        owner = self

        class Output:
            def start(self):
                assert owner.input_open
                assert owner.capture_mode() == "MUTED_HALF_DUPLEX"
                owner.software_muted_before_playback = True

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
        self.device = LocalAudioDevice(backend=self.backend, policy=self.policy, clock=self.clock)
        self.backend.capture_mode = lambda: self.device.capture_mode
        safety = SafetyService()
        self.store = InMemoryVoiceTurnStore()
        self.voice = VoicePrototype(
            self.state, TextConversationService(MockLLMProvider(response="नमस्ते।"), safety=safety),
            SpeechSynthesisService(SyntheticPCMProvider(), safety, model_version="synthetic-v1"),
            self.device, self.store, clock=self.clock,
        )
        self.device.bind_epoch_guard(self.voice.epochs)
        self.speech.epochs = self.voice.epochs
        original_feed = self.speech.feed

        async def feed(pcm, **kwargs):
            results = await original_feed(pcm, **kwargs)
            if (not results and self.device.capture_mode == "ACTIVE"
                    and self.clock() < self._capture_deadline):
                self.backend.emit_next()
            return results

        self.speech.feed = feed
        self.last_transcript = None
        self.last_timings = None

    async def __aenter__(self):
        await self.device.start()
        assert self.device.capture_mode == "MUTED_HALF_DUPLEX"
        return self

    async def __aexit__(self, *exc):
        await self.device.aclose()
        assert not self.backend.input_open
        assert self.backend.input_created == self.backend.input_starts == self.backend.input_closes == 1

    @staticmethod
    def audio(*segments):
        return b"".join(array("h", [marker]) .tobytes() * int(milliseconds * 16)
                        for marker, milliseconds in segments)

    async def capture(self, pcm, *, max_seconds=30):
        self.backend.pcm, self.backend.offset = pcm, 0
        self._capture_deadline = self.clock() + max_seconds
        epoch = self.voice.epochs.advance()
        with patch("roma.providers.audio.local_device.perf_counter", self.clock), patch(
            "roma.services.speech_input.perf_counter", self.clock
        ):
            self.last_transcript, self.last_timings = await self.device.listen(
                self.speech, max_seconds=max_seconds, epoch=epoch,
                on_capture_started=self.backend.emit_next,
            )
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
