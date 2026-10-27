"""Explicit local-HTTP speech adapters for ordered Twilio WebSocket PCM.

No cloud speech fallback. Model loading belongs to the separately warmed worker.
"""
import asyncio
from contextlib import suppress
from datetime import UTC, datetime
from uuid import uuid4

from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    ErrorFrame,
    InputAudioRawFrame,
    InterruptionFrame,
    TextFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from roma.core.turn_epoch import TurnEpoch, epoch_scope
from roma.domain.audio.pcm import validate_pcm16
from roma.domain.audio.resampling import PCMResampler
from roma.domain.safety.audit import SafetyContext
from roma.domain.safety.lexicon import HARD_FAIL_LINE
from roma.providers.ai.contracts import AudioFormat, STTRequest, TTSRequest
from roma.realtime.playback_window import PlaybackPressure
from roma.services.safety_service import SafetyService
from roma.services.tts_service import SpeechSynthesisService
from roma.services.voice_pronunciation import render_voice_sentence


def resample_utterance(audio, source, target):
    converter = PCMResampler(source, target)
    return converter.feed(audio) + converter.finish()


async def prime_local_deferral(cache, provider):
    """Readiness-only native synthesis; callers later need no inference for deferral."""
    from roma.domain.knowledge.routing import NO_EVIDENCE_LINE
    safety = SafetyService()
    context = SafetyContext('speech-readiness', 1, 'value')
    checked = await safety.check(NO_EVIDENCE_LINE, context)
    if checked.text != NO_EVIDENCE_LINE or not checked.audit_ok:
        raise ValueError('Deferral policy unavailable')
    service = SpeechSynthesisService(provider, safety, model_version='served-indic-tts-v1')
    rendered = render_voice_sentence(checked.text, None)
    speech = await service.synthesize(TTSRequest(rendered, format=AudioFormat(8000)), context)
    cache.install_knowledge_deferral(speech.result.audio)


class LocalSTTProcessor(FrameProcessor):
    def __init__(self, provider, *, epoch=None, max_audio_secs=30, language_hint=None):
        super().__init__()
        self.provider, self.epoch = provider, epoch or TurnEpoch()
        self.usage_sink = None
        self.usage_turn_snapshot = None
        self.max_audio_secs, self.language_hint = max_audio_secs, language_hint
        self._audio, self._preroll = bytearray(), bytearray()
        self._active, self._rate, self._task = False, 8000, None

    async def decode(self, audio, rate):
        epoch, request_id = self.epoch.current, str(uuid4())
        turn = self.usage_turn_snapshot() if self.usage_turn_snapshot else None
        interrupted = False
        with epoch_scope(self.epoch, epoch):
            try:
                pcm = await asyncio.to_thread(resample_utterance, audio, rate, 16000)
                self.epoch.require_current(epoch)
                result = await self.provider.transcribe(STTRequest(pcm, AudioFormat(16000),
                    self.language_hint, turn_epoch=epoch))
                self.epoch.require_current(epoch)
                if result.turn_epoch is not None and result.turn_epoch != epoch:
                    raise asyncio.CancelledError('Late ASR')
                if result.error_code or not result.text.strip():
                    await self.push_frame(ErrorFrame('Local speech transcription unavailable'))
                    return
                await self.push_frame(TranscriptionFrame(result.text, '', datetime.now(UTC).isoformat()))
            except asyncio.CancelledError:
                interrupted = True
                raise
            finally:
                if self.usage_sink is not None:
                    try:
                        await self.usage_sink('stt', request_id, audio_seconds=len(audio)/2/rate,
                            model='whisper-turbo-structural-v3', **({'turn': turn} if turn is not None else {}))
                    except Exception:
                        if not interrupted:
                            raise

    async def _decode(self, audio, rate):
        try:
            await self.decode(audio, rate)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — errors carry no audio, text, provider body or URL.
            await self.push_frame(ErrorFrame('Local speech transcription unavailable'))

    async def _cancel(self):
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if direction != FrameDirection.DOWNSTREAM:
            await self.push_frame(frame, direction)
            return
        if isinstance(frame, VADUserStartedSpeakingFrame):
            await self._cancel()
            self._active = True
            self._audio = bytearray(self._preroll)
            self._preroll.clear()
        elif isinstance(frame, InputAudioRawFrame):
            validate_pcm16(frame.audio)
            if frame.num_channels != 1 or frame.sample_rate not in (8000, 16000):
                await self.push_frame(ErrorFrame('Unsupported local speech media', fatal=True))
                return
            if self._audio and frame.sample_rate != self._rate:
                await self.push_frame(ErrorFrame('Speech sample rate changed', fatal=True))
                return
            self._rate = frame.sample_rate
            if self._active:
                if len(self._audio) + len(frame.audio) > self._rate * 2 * self.max_audio_secs:
                    self._audio.clear()
                    self._active = False
                    await self.push_frame(ErrorFrame('Speech utterance capacity exceeded', fatal=True))
                    return
                self._audio.extend(frame.audio)
            else:
                self._preroll.extend(frame.audio)
                del self._preroll[:-self._rate // 5]  # 100 ms of PCM16, bounded pre-roll
        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            self._active = False
            audio, self._audio = bytes(self._audio), bytearray()
            if audio:
                self._task = asyncio.create_task(self._decode(audio, self._rate))
        elif isinstance(frame, InterruptionFrame):
            # The downstream controller owns the shared interruption epoch.
            await self._cancel()  # retain the current near-end utterance
        elif isinstance(frame, (EndFrame, CancelFrame)):
            await self._cancel()
            self._audio.clear()
            self._preroll.clear()
        await self.push_frame(frame, direction)  # preserve caller leg for stereo recording

    async def cleanup(self):
        await self._cancel()
        close = getattr(self.provider, 'aclose', None)
        if close:
            await close()
        await super().cleanup()


class LocalTTSProcessor(FrameProcessor):
    def __init__(self, provider, *, phrases=None, state=None, safety=None, epoch=None):
        super().__init__()
        self.provider, self.phrases, self.state = provider, phrases, state
        self.safety, self.epoch = safety or SafetyService(), epoch or TurnEpoch()
        self.speech = SpeechSynthesisService(provider, self.safety, model_version='served-indic-tts-v1')
        self.phrase_hits = self.phrase_chars = 0
        self.playback_window = None
        self.usage_sink = None
        self.usage_turn_snapshot = None

    async def speak(self, text):
        epoch, context_id = self.epoch.current, str(uuid4())
        turn = self.usage_turn_snapshot() if self.usage_turn_snapshot else None
        interrupted = False
        state = self.state
        context = SafetyContext(state.call_sid if state else 'local-speech',
            max(1, state.turn_count) if state else 1, state.stage.value if state else 'value')
        pcm, cache_hit, characters = None, False, len(text)
        with epoch_scope(self.epoch, epoch):
            try:
                checked = await self.safety.check(text, context)
                canonical, characters = checked.text, len(checked.text)
                pcm = self.phrases.lookup(canonical) if self.phrases else None
                cache_hit = pcm is not None
                if cache_hit:
                    self.phrase_hits += 1
                    self.phrase_chars += len(canonical)
                else:
                    rendered = render_voice_sentence(canonical, state) if state else canonical
                    speech = await self.speech.synthesize(
                        TTSRequest(rendered, format=AudioFormat(8000), turn_epoch=epoch), context)
                    pcm = speech.result.audio
                    cache_hit = speech.cache_hit
                self.epoch.require_current(epoch)
                await self.push_frame(TTSStartedFrame(context_id=context_id))
                for offset in range(0, len(pcm), 320):
                    self.epoch.require_current(epoch)
                    frame = TTSAudioRawFrame(pcm[offset:offset+320].ljust(320, b'\0'), 8000, 1, context_id=context_id)
                    if self.playback_window is not None:
                        generation = await self.playback_window.reserve(len(frame.audio))
                        self.epoch.require_current(epoch)
                        frame._roma_playback_generation = generation
                    await self.push_frame(frame)
                await self.push_frame(TTSStoppedFrame(context_id=context_id))
            except asyncio.CancelledError:
                interrupted = True
                raise
            finally:
                if self.usage_sink is not None:
                    try:
                        await self.usage_sink('tts', context_id, audio_seconds=len(pcm)/16000 if pcm is not None else None,
                            characters=characters, cache_hit=cache_hit, model='indic-tts',
                            **({'turn': turn} if turn is not None else {}))
                    except Exception:
                        if not interrupted:
                            raise

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if direction == FrameDirection.DOWNSTREAM and isinstance(frame, TextFrame):
            epoch = self.epoch.current
            try:
                await self.speak(frame.text)
            except asyncio.CancelledError:
                raise
            except PlaybackPressure:
                return  # The window has already requested call cancellation/close.
            except Exception:  # noqa: BLE001 — only the reviewed cached outage line may play.
                self.epoch.require_current(epoch)
                fallback = self.phrases.lookup(HARD_FAIL_LINE) if self.phrases else None
                if fallback:
                    await self.push_frame(TTSAudioRawFrame(fallback, 8000, 1))
                else:
                    await self.push_frame(ErrorFrame('Local speech synthesis unavailable', fatal=True))
                return
        await self.push_frame(frame, direction)

    async def cleanup(self):
        close = getattr(self.provider, 'aclose', None)
        if close:
            await close()
        await super().cleanup()
