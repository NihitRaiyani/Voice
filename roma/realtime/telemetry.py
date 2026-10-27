"""Bounded per-call probes over real Pipecat stage boundaries.

The turn clock starts at VAD stop confirmation (or context admission for opening
turns) and ends at the transport's software playback-complete notification.
Endpointing is Pipecat's reported VAD silence window, not acoustic latency.
"""

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    CancelFrame,
    EndFrame,
    InterruptionFrame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TextFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    TTSSpeakFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from roma.core.telemetry import TurnTelemetry, get_telemetry

BOUNDARIES = frozenset({
    'stt_input', 'stt_output', 'brain_input', 'brain_output', 'llm_input',
    'llm_output', 'tts_input', 'tts_output', 'output',
})


class RealtimeTurnTelemetry:
    """Shared by a call's probes; keeps clocks and flags, never frame payloads."""

    def __init__(self, telemetry=None):
        self.telemetry = telemetry or get_telemetry()
        self.turn = None
        self._audio_observed = False
        self._response_open = False
        self._playback_stopped = False

    def _begin(self):
        if self.turn is None:
            self.turn = TurnTelemetry(self.telemetry)
            self.turn.start('turn')
            self._audio_observed = False
            self._response_open = False
            self._playback_stopped = False

    def _complete(self):
        if (self.turn is not None and self._audio_observed
                and self._playback_stopped and not self._response_open):
            self.turn.finish('turn')
            self.telemetry.event('turn')
            self.turn.cancel()
            self.turn = None

    def cancel(self):
        if self.turn is not None:
            self.turn.cancel()
            self.telemetry.event('turn', outcome='cancelled')
            self.turn = None
        self._audio_observed = False
        self._response_open = False
        self._playback_stopped = False

    def observe(self, boundary, frame, direction=FrameDirection.DOWNSTREAM):
        if boundary not in BOUNDARIES:
            raise ValueError('Use a reviewed telemetry boundary')
        if isinstance(frame, (CancelFrame, EndFrame, InterruptionFrame)):
            self.cancel()
            return
        if boundary == 'output' and isinstance(frame, BotStoppedSpeakingFrame):
            self._playback_stopped = True
            self._complete()
            return
        if boundary == 'output' and isinstance(frame, BotStartedSpeakingFrame):
            self._playback_stopped = False
            return
        if direction != FrameDirection.DOWNSTREAM:
            return
        if boundary in {'llm_output', 'tts_input'} and isinstance(frame, LLMFullResponseStartFrame):
            self._begin()
            self._response_open = True
        elif boundary == 'output' and isinstance(frame, LLMFullResponseEndFrame):
            # Transport serializes this behind preceding audio. A sentence's
            # BotStoppedSpeakingFrame alone does not complete a streamed turn.
            self._response_open = False
            self._complete()
        elif boundary == 'stt_input' and isinstance(frame, VADUserStoppedSpeakingFrame):
            self.cancel()
            self._begin()
            self.turn.start('stt')
            self.telemetry.observe_latency('endpointing', frame.stop_secs)
        elif boundary == 'stt_output' and isinstance(frame, TranscriptionFrame):
            if self.turn is not None:
                self.turn.finish('stt')
        elif boundary == 'brain_input' and isinstance(frame, LLMContextFrame):
            self._begin()
            self.turn.start('brain')
        elif boundary == 'brain_output' and isinstance(frame, (LLMContextFrame, LLMFullResponseStartFrame)):
            if self.turn is not None:
                self.turn.finish('brain')
        elif boundary == 'llm_input' and isinstance(frame, LLMContextFrame):
            self._begin()
            self.turn.start('llm_first')
        elif boundary == 'llm_output' and isinstance(frame, LLMTextFrame) and frame.text:
            if self.turn is not None:
                self.turn.finish('llm_first')
        elif boundary == 'tts_input' and isinstance(frame, (TextFrame, TTSSpeakFrame)) and frame.text:
            if self.turn is not None:
                self.turn.start('tts_first')
        elif boundary == 'tts_output' and isinstance(frame, TTSAudioRawFrame) and frame.audio:
            if self.turn is not None:
                self.turn.finish('tts_first')
                self._audio_observed = True
                self._playback_stopped = False


class TelemetryFrameProbe(FrameProcessor):
    def __init__(self, turns, *, boundary, **kwargs):
        if boundary not in BOUNDARIES:
            raise ValueError('Use a reviewed telemetry boundary')
        super().__init__(**kwargs)
        self.turns, self.boundary = turns, boundary

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        self.turns.observe(self.boundary, frame, direction)
        await self.push_frame(frame, direction)

    async def cleanup(self):
        self.turns.cancel()
        await super().cleanup()
