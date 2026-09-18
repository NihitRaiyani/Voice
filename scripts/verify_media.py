#!/usr/bin/env python3
"""Offline end-to-end check for signed Twilio bidirectional Media Streams."""

import base64
import json
import os
import sys
import threading
from xml.etree import ElementTree

from pipecat.frames.frames import InputAudioRawFrame, OutputAudioRawFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from starlette.testclient import TestClient
from twilio.request_validator import RequestValidator

ACCOUNT_SID = "AC" + "1" * 32
AUTH_TOKEN = "offline-test-token"
PUBLIC_BASE_URL = "https://testserver"
STREAM_SID = "MZ_synthetic"
CALL_SID = "CA_synthetic"

os.environ.setdefault("TWILIO_ACCOUNT_SID", ACCOUNT_SID)
os.environ.setdefault("TWILIO_AUTH_TOKEN", AUTH_TOKEN)
os.environ.setdefault("TWILIO_FROM_NUMBER", "+16295550100")
os.environ.setdefault("SARVAM_API_KEY", "sarvam_test")
os.environ.setdefault("OPENAI_API_KEY", "openai_test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("PUBLIC_BASE_URL", PUBLIC_BASE_URL)

N_INBOUND = 5
SILENCE_ULAW = bytes([0xFF] * 160)
WATCHDOG_SECS = 45.0


class _Passthrough(FrameProcessor):
    async def process_frame(self, frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)


class _EchoTTS(FrameProcessor):
    async def process_frame(self, frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)
        if isinstance(frame, InputAudioRawFrame):
            await self.push_frame(
                OutputAudioRawFrame(
                    audio=b"\x00\x00" * 160, sample_rate=8000, num_channels=1
                ),
                FrameDirection.DOWNSTREAM,
            )


def _give_up() -> None:
    print("RESULT: FAIL (synthetic media check timed out)", flush=True)
    os._exit(2)


def main() -> int:
    from roma.telephony.media import build_media_app, sole_call

    timer = threading.Timer(WATCHDOG_SECS, _give_up)
    timer.daemon = True
    timer.start()
    app = build_media_app(
        auto_hang_up=False,
        build_stt_fn=lambda _s: _Passthrough(),
        build_vad_fn=lambda _s: None,
        build_llm_fn=lambda _s: _Passthrough(),
        build_tts_fn=lambda _s, _cache=None: _EchoTTS(),
    )
    validator = RequestValidator(AUTH_TOKEN)
    with TestClient(app) as client:
        params = {"AccountSid": ACCOUNT_SID, "CallSid": CALL_SID}
        answer_signature = validator.compute_signature(
            f"{PUBLIC_BASE_URL}/answer", params
        )
        answer = client.post(
            "/answer",
            data=params,
            headers={"X-Twilio-Signature": answer_signature},
        )
        assert answer.status_code == 200, f"/answer returned {answer.status_code}"
        root = ElementTree.fromstring(answer.text)
        stream = root.find("./Connect/Stream")
        assert stream is not None and stream.attrib["url"] == "wss://testserver/ws"

        ws_signature = validator.compute_signature("wss://testserver/ws", {})
        with client.websocket_connect(
            "/ws", headers={"X-Twilio-Signature": ws_signature}
        ) as ws:
            ws.send_text(
                json.dumps(
                    {
                        "event": "start",
                        "sequenceNumber": "1",
                        "streamSid": STREAM_SID,
                        "start": {
                            "accountSid": ACCOUNT_SID,
                            "callSid": CALL_SID,
                            "streamSid": STREAM_SID,
                            "mediaFormat": {
                                "encoding": "audio/x-mulaw",
                                "sampleRate": 8000,
                                "channels": 1,
                            },
                            "customParameters": {},
                        },
                    }
                )
            )
            payload = base64.b64encode(SILENCE_ULAW).decode()
            for sequence in range(2, N_INBOUND + 2):
                ws.send_text(
                    json.dumps(
                        {
                            "event": "media",
                            "sequenceNumber": str(sequence),
                            "streamSid": STREAM_SID,
                            "media": {
                                "track": "inbound",
                                "chunk": str(sequence - 1),
                                "timestamp": str((sequence - 2) * 20),
                                "payload": payload,
                            },
                        }
                    )
                )

            outbound = 0
            for _ in range(40):
                message = json.loads(ws.receive_text())
                if message.get("event") == "media":
                    assert message.get("streamSid") == STREAM_SID
                    assert message["media"]["payload"]
                    outbound += 1
                if outbound >= 5:
                    break

            handles = sole_call(app)
            counter = handles.counter
            ok = counter.frame_count >= N_INBOUND and outbound >= 5
            print(f"audio-IN : {counter.frame_count} frames")
            print(f"audio-OUT: {outbound} Twilio media events")
            print("RESULT:", "PASS" if ok else "FAIL")
            sys.stdout.flush()
            os._exit(0 if ok else 1)


if __name__ == "__main__":
    main()
