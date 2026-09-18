"""The small Pipecat/Twilio wire contract Roma depends on."""

import asyncio
import json

from pipecat.frames.frames import InterruptionFrame, OutputAudioRawFrame, StartFrame
from pipecat.serializers.twilio import TwilioFrameSerializer


def _serializer():
    return TwilioFrameSerializer(
        stream_sid="MZ123",
        params=TwilioFrameSerializer.InputParams(auto_hang_up=False),
    )


def test_output_audio_uses_twilio_media_envelope():
    async def run():
        serializer = _serializer()
        await serializer.setup(
            StartFrame(audio_in_sample_rate=8000, audio_out_sample_rate=8000)
        )
        return json.loads(
            await serializer.serialize(
                OutputAudioRawFrame(
                    audio=b"\x00\x01" * 80, sample_rate=8000, num_channels=1
                )
            )
        )

    message = asyncio.run(run())
    assert message["event"] == "media"
    assert message["streamSid"] == "MZ123"
    assert message["media"]["payload"]


def test_interruption_clears_twilio_buffer():
    async def run():
        return await _serializer().serialize(InterruptionFrame())

    assert json.loads(asyncio.run(run())) == {
        "event": "clear",
        "streamSid": "MZ123",
    }
