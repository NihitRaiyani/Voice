import asyncio
import base64
import json

from pipecat.frames.frames import (
    InputAudioRawFrame,
    InterruptionFrame,
    OutputAudioRawFrame,
    StartFrame,
)
from pipecat.serializers.twilio import TwilioFrameSerializer
from roma.providers.telephony.twilio.audio import ValidatedTwilioSerializer


def message(sequence=2, chunk=1, payload=None, **values):
    return json.dumps(
        {
            "event": "media",
            "streamSid": "MZsynthetic",
            "sequenceNumber": str(sequence),
            "media": {
                "track": "inbound",
                "chunk": str(chunk),
                "timestamp": str((chunk - 1) * 20),
                "payload": base64.b64encode(bytes([255]) * 160).decode()
                if payload is None
                else payload,
            },
            **values,
        }
    )


async def serializer():
    result = ValidatedTwilioSerializer(
        stream_sid="MZsynthetic", params=TwilioFrameSerializer.InputParams(auto_hang_up=False)
    )
    await result.setup(StartFrame(audio_in_sample_rate=8000, audio_out_sample_rate=8000))
    return result


def test_sequence_gate_precedes_native_decode_and_counts_media_not_control_gaps(monkeypatch):
    async def run():
        codec = await serializer()
        native = TwilioFrameSerializer.deserialize
        calls = []

        async def watched(self, data):
            calls.append(data)
            return await native(self, data)

        monkeypatch.setattr(TwilioFrameSerializer, "deserialize", watched)
        assert isinstance(await codec.deserialize(message()), InputAudioRawFrame)
        assert await codec.deserialize(message()) is None
        assert len(calls) == 1  # rejected before the decoder is called
        await codec.deserialize(
            json.dumps({"event": "mark", "sequenceNumber": "3", "mark": {"name": "test"}})
        )
        assert await codec.deserialize(message(sequence=4, chunk=2)) is not None
        assert codec.audio_metrics.sequence_gaps == 0
        assert await codec.deserialize(message(sequence=6, chunk=4)) is not None
        assert codec.audio_metrics.sequence_gaps == 1
        assert codec.audio_metrics.duplicates_or_reordered == 1

    asyncio.run(run())


def test_variable_payloads_and_bad_inputs_do_not_break_alignment():
    async def run():
        codec = await serializer()
        for size in (80, 160, 320):
            fresh = await serializer()
            frame = await fresh.deserialize(
                message(payload=base64.b64encode(bytes([255]) * size).decode())
            )
            assert len(frame.audio) == size * 2 and frame.sample_rate == 8000
        for raw in (
            "[]",
            "not-json",
            message(streamSid="other"),
            message(payload="%%%"),
            message(payload=""),
            message(sequence=0),
            message(payload=base64.b64encode(bytes(8001)).decode()),
        ):
            assert await codec.deserialize(raw) is None
        assert codec.audio_metrics.invalid_messages == 7
        frame = await codec.deserialize(message())
        assert frame.audio == bytes(320) and codec.audio_metrics.sequence_gaps == 0

    asyncio.run(run())


def test_native_output_and_clear_are_preserved_and_metrics_are_isolated():
    async def run():
        first, second = await serializer(), await serializer()
        await first.deserialize(message(sequence=3, chunk=2))
        assert (
            first.audio_metrics.sequence_gaps == 1 and second.audio_metrics.sequence_gaps == 0
        )
        clear = json.loads(await first.serialize(InterruptionFrame()))
        assert clear == {"event": "clear", "streamSid": "MZsynthetic"}
        output = json.loads(await first.serialize(OutputAudioRawFrame(bytes(320), 8000, 1)))
        assert base64.b64decode(output["media"]["payload"]) == bytes([255]) * 160

    asyncio.run(run())


def test_odd_native_pcm_is_rejected_before_vad(monkeypatch):
    async def run():
        codec = await serializer()

        async def odd(self, data):
            return InputAudioRawFrame(audio=b"x", sample_rate=8000, num_channels=1)

        monkeypatch.setattr(TwilioFrameSerializer, "deserialize", odd)
        assert await codec.deserialize(message()) is None
        assert codec.audio_metrics.pcm_alignment_errors == 1
        assert codec.audio_metrics.accepted_media == 0

    asyncio.run(run())


def test_timestamp_regression_is_rejected_without_consuming_a_valid_chunk():
    async def run():
        codec = await serializer()
        first = json.loads(message())
        first["media"]["timestamp"] = "100"
        assert await codec.deserialize(json.dumps(first)) is not None
        second = json.loads(message(sequence=3, chunk=2))
        second["media"]["timestamp"] = "90"
        assert await codec.deserialize(json.dumps(second)) is None
        assert codec.audio_metrics.invalid_messages == 1
        second["media"]["timestamp"] = "120"
        assert await codec.deserialize(json.dumps(second)) is not None
        assert codec.audio_metrics.sequence_gaps == 0

    asyncio.run(run())


def test_control_messages_advance_global_order_without_creating_media_gaps():
    async def run():
        codec = await serializer()
        await codec.deserialize(message())
        await codec.deserialize(
            json.dumps({"event": "mark", "sequenceNumber": "10", "mark": {"name": "synthetic"}})
        )
        assert await codec.deserialize(message(sequence=9, chunk=2)) is None
        assert codec.audio_metrics.sequence_gaps == 0
        assert codec.audio_metrics.duplicates_or_reordered == 1
        assert await codec.deserialize(message(sequence=11, chunk=2)) is not None
        assert codec.audio_metrics.sequence_gaps == 0

    asyncio.run(run())


def test_malformed_control_does_not_poison_the_sequence_counter():
    async def run():
        codec = await serializer()
        assert await codec.deserialize(json.dumps({"sequenceNumber": "999"})) is None
        assert codec.audio_metrics.invalid_messages == 1
        assert await codec.deserialize(message()) is not None
        assert codec.audio_metrics.accepted_media == 1

    asyncio.run(run())
