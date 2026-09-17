"""The VAD must actually be in the pipeline (live regression, four calls, 2026-07-26).

`media.py` passed `vad_analyzer=build_vad(settings)` into `FastAPIWebsocketParams(...)`.
In pipecat 1.6.0 `vad_analyzer` is not a field on `TransportParams` — a pydantic `BaseModel`
carrying pydantic's default `extra='ignore'` — so it was accepted by the constructor and
silently dropped. Four live calls ran with no VAD at all: zero barge-in decisions, zero
backchannel suppressions, endpointing on STT alone, and Sarvam never flushed.

## Why the existing tests all passed

They fake the wrong end. `test_media_isolation.py` injects `build_vad_fn=lambda _s: None`,
so the offline path never had a VAD either — the bug and the test agreed. `test_turntaking.py`
hand-constructs every `VADUserStartedSpeakingFrame` it feeds in, which proves the strategy's
LOGIC and can say nothing about whether anything ever emits one.

So these tests invert it: **the wiring is real and only the leaf is faked.** A real
`Pipeline`, the real `VADProcessor`, the real `VADAnalyzer` state machine — and a stub only
at the very bottom, where the neural net would be.
"""

import asyncio

from pipecat.audio.vad.vad_analyzer import VADAnalyzer, VADParams
from pipecat.frames.frames import (
    InputAudioRawFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams

from roma.config import Settings
from roma.telephony.media import InboundAudioCounter, build_vad, vad_stage

RATE = 8000
CHUNK = 256


def test_transport_params_still_have_no_vad_analyzer_field():
    """The assertion that would have caught it on day one.

    Written as a POSITIVE pin rather than a comment: if a future pipecat adds the field
    back, this fails and someone re-reads the wiring — instead of the code silently
    changing meaning under us in the other direction."""
    assert "vad_analyzer" not in FastAPIWebsocketParams.model_fields


def test_passing_vad_analyzer_to_transport_params_is_silently_discarded():
    """The exact mechanism: constructed happily, dropped without a word."""
    params = FastAPIWebsocketParams(audio_in_enabled=True, vad_analyzer="SENTINEL")
    assert not hasattr(params, "vad_analyzer")
    assert params.model_extra is None


def test_no_analyzer_means_an_empty_stage():
    """`vad_stage(None)` is what the offline tests and verify_media use. Empty list =
    no VAD, stated as a contract so it cannot be true by accident again."""
    assert vad_stage(None) == []


def test_an_analyzer_becomes_exactly_one_processor():
    stage = vad_stage(build_vad(Settings()))
    assert len(stage) == 1
    assert isinstance(stage[0], FrameProcessor)


def test_the_stage_carries_the_tuned_stop_secs():
    """`vad_stop_secs` is THE Step-7 knob. It was printed in the teardown line and applied
    to nothing for four calls."""
    settings = Settings()
    analyzer = build_vad(settings)
    assert analyzer.params.stop_secs == settings.vad_stop_secs


class _EnergyVAD(VADAnalyzer):
    """Speech = loud. Substitutes for Silero's neural net and NOTHING else.

    Subclassing the real `VADAnalyzer` means `analyze_audio`'s start/stop state machine —
    including the `stop_secs` hangover — is the real one, so these tests also prove
    `VADParams(stop_secs=...)` is actually applied rather than merely stored.
    """

    def num_frames_required(self) -> int:
        return CHUNK

    def voice_confidence(self, buffer) -> float:
        if not buffer:
            return 0.0
        peak = max(
            abs(int.from_bytes(buffer[i : i + 2], "little", signed=True))
            for i in range(0, len(buffer) - 1, 2)
        )
        return 1.0 if peak > 8000 else 0.0


def _pcm(amplitude: int, chunks: int) -> list:
    """`chunks` frames of a square wave (or silence when amplitude is 0)."""
    sample = int(amplitude).to_bytes(2, "little", signed=True)
    return [
        InputAudioRawFrame(audio=sample * CHUNK, sample_rate=RATE, num_channels=1)
        for _ in range(chunks)
    ]


class _Sink(FrameProcessor):
    """Stands in for STT: records the VAD frames it OBSERVES, which is the flush path."""

    def __init__(self) -> None:
        super().__init__()
        self.seen = []

    async def process_frame(self, frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        self.seen.append(frame)
        await self.push_frame(frame, direction)


def _run(processors, frames):
    from pipecat.frames.frames import EndFrame, StartFrame
    from pipecat.pipeline.runner import PipelineRunner
    from pipecat.pipeline.task import PipelineTask

    pipeline = Pipeline(processors)
    task = PipelineTask(pipeline)

    async def go():
        runner = PipelineRunner(handle_sigint=False)
        run = asyncio.create_task(runner.run(task))
        for f in frames:
            await task.queue_frame(f)
        await task.queue_frame(EndFrame())
        await run

    asyncio.run(go())
    _ = StartFrame


def test_the_vad_stage_produces_start_and_stop_frames_from_real_audio():
    """THE regression. Not "is the strategy's logic right" — "does anything emit a VAD
    frame at all". The answer was no, for four calls, and no test could tell."""
    settings = Settings()
    analyzer = _EnergyVAD(
        sample_rate=RATE,
        params=VADParams(stop_secs=settings.vad_stop_secs, start_secs=0.0, min_volume=0.0),
    )
    sink = _Sink()
    counter = InboundAudioCounter()

    frames = _pcm(20000, 20) + _pcm(0, 60)
    _run([*vad_stage(analyzer), counter, sink], frames)

    assert any(isinstance(f, VADUserStartedSpeakingFrame) for f in sink.seen), (
        "no VADUserStartedSpeakingFrame reached STT — barge-in cannot work"
    )
    assert any(isinstance(f, VADUserStoppedSpeakingFrame) for f in sink.seen), (
        "no VADUserStoppedSpeakingFrame reached STT — Sarvam never flushes (stt.py:444)"
    )


def test_the_counter_sees_each_vad_event_exactly_once():
    """The teardown line's honesty depends on this. The counter sits immediately after the
    stage, so the upstream sibling never reaches it and no direction filtering is needed."""
    analyzer = _EnergyVAD(
        sample_rate=RATE, params=VADParams(stop_secs=0.2, start_secs=0.0, min_volume=0.0)
    )
    counter = InboundAudioCounter()
    _run([*vad_stage(analyzer), counter, _Sink()], _pcm(20000, 20) + _pcm(0, 60))

    assert counter.vad_starts == 1, counter.vad_starts
    assert counter.vad_stops == 1, counter.vad_stops
    assert counter.frame_count == 80


def test_an_empty_stage_produces_no_vad_frames_at_all():
    """The mirror image, and the state the system was actually in: audio flows, nothing
    else happens. Without this the test above could pass for the wrong reason."""
    counter = InboundAudioCounter()
    sink = _Sink()
    _run([*vad_stage(None), counter, sink], _pcm(20000, 20) + _pcm(0, 60))

    assert counter.frame_count == 80
    assert counter.vad_starts == 0 and counter.vad_stops == 0
    assert not any(isinstance(f, VADUserStartedSpeakingFrame) for f in sink.seen)


def _media_ast():
    import ast
    from pathlib import Path

    import roma.telephony.media as m

    return ast.parse(Path(m.__file__).read_text(encoding="utf-8"))


def _calls_named(tree, name: str):
    import ast

    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", getattr(node.func, "attr", None)) == name
    ]


def test_the_analyzer_is_never_handed_to_the_transport_again():
    """`FastAPIWebsocketParams(vad_analyzer=...)` is the bug, verbatim — pydantic accepts
    the keyword and drops it. Assert on the real keywords of the real call."""
    calls = _calls_named(_media_ast(), "FastAPIWebsocketParams")
    assert calls, "no FastAPIWebsocketParams call found — has the transport moved?"
    for call in calls:
        kwargs = {k.arg for k in call.keywords}
        assert "vad_analyzer" not in kwargs, (
            "the VAD analyzer is a transport param again; pydantic will discard it silently"
        )
        assert "audio_in_enabled" in kwargs


def test_the_stage_is_in_the_pipeline_and_above_stt():
    """Order is the load-bearing part: below `stt`, Sarvam never sees the stop frame it
    flushes on, and the fix would be half a fix."""
    import ast

    pipelines = _calls_named(_media_ast(), "Pipeline")
    assert pipelines, "no Pipeline call found"
    elements = pipelines[0].args[0].elts

    def _label(el) -> str:
        if isinstance(el, ast.Starred):
            inner = el.value
            return (
                getattr(inner.func, "id", "*call") if isinstance(inner, ast.Call) else "*expr"
            )
        if isinstance(el, ast.Name):
            return el.id
        if isinstance(el, ast.Call):
            return getattr(el.func, "attr", getattr(el.func, "id", "call"))
        return "?"

    names = [_label(el) for el in elements]

    assert "vad_stage" in names, f"the VAD stage is not in the pipeline: {names}"
    assert names.index("vad_stage") < names.index("stt"), f"VAD must precede STT: {names}"
    assert names.index("vad_stage") < names.index("counter"), (
        f"VAD must precede the counter, or the event counts double-count: {names}"
    )
