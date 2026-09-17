#!/usr/bin/env python3
"""Synthetic Vobiz `<Stream>` end-to-end check (no live call, no spend).

THIS FILE IS THE EXECUTABLE SPEC OF THE WIRE PROTOCOL. It hand-writes the exact JSON Vobiz
sends and asserts on the exact JSON Roma sends back, so a mistake in the envelope is caught
here rather than on a live call after a lead has picked up. Budget is ₹22 — that difference
matters.

It drives the real FastAPI app and the real Pipecat transport through Starlette's TestClient:

    POST /answer                      -> XML naming the socket, carrying a one-use token
    GET  /ws?t=<token>                -> Vobiz's side of the socket
      -> start   (with mediaFormat)   -> the serializer adopts the declared inbound format
      -> media*N (base64 μ-law)       -> audio-IN,  counted by InboundAudioCounter
      <- playAudio*                   -> audio-OUT, the canned consent line

Run: uv run python scripts/verify_media.py
"""

import base64
import json
import os
import re
import sys
import threading

# No VOBIZ_AUTH_ID / VOBIZ_AUTH_TOKEN, deliberately: this script drives the inbound path,
# which does not use the account REST API at all. Faking them here would hide a dependency
# rather than prove there is none.
os.environ.setdefault("VOBIZ_FROM_NUMBER", "+917971543192")
os.environ.setdefault("SARVAM_API_KEY", "sarvam_test")
os.environ.setdefault("OPENAI_API_KEY", "openai_test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

from pipecat.frames.frames import InputAudioRawFrame, OutputAudioRawFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from starlette.testclient import TestClient

from roma.telephony.media import build_media_app, sole_call

N_INBOUND = 5
SILENCE_ULAW = bytes([0xFF] * 160)  # 20ms of μ-law silence, the chunk size Vobiz documents


class _Passthrough(FrameProcessor):
    """Offline stand-in for a network service (STT/LLM) — forwards every frame."""

    async def process_frame(self, frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)


class _EchoTTS(FrameProcessor):
    """Stands in for Bulbul: emits one frame of audio for each frame it receives.

    The script used to rely on the canned consent line for its outbound audio, and that has
    not been emitted for a long time: `consent_signed_off()` is False while CONSENT_LINE is
    the compliance placeholder, so `app.state.consent_line` is None and the pipeline speaks
    nothing (`media.py`, and the test that pins it). The audio-OUT half of this check was
    therefore unobservable — it would block forever waiting for a frame that by design never
    came.

    Generating the audio here instead makes the check independent of a compliance gate it was
    never meant to be testing, and still exercises the real path that matters: the real
    transport, the real serializer, and the real websocket.
    """

    async def process_frame(self, frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)
        if isinstance(frame, InputAudioRawFrame):
            await self.push_frame(
                OutputAudioRawFrame(audio=b"\x00\x00" * 160, sample_rate=8000, num_channels=1),
                FrameDirection.DOWNSTREAM,
            )


# Starlette's TestClient deadlocks its synchronous portal when the full pipeline is driven
# through it — a limitation this repo already documents in tests/telephony/test_media_isolation
# .py. Rather than hang forever when that happens, fail loudly with a time bound.
WATCHDOG_SECS = 45.0


def _give_up() -> None:
    print(
        "RESULT: FAIL (timed out)\n"
        "  The pipeline produced no outbound audio within the time bound. Known causes:\n"
        "  * TestClient's portal deadlocks on the full pipeline (see test_media_isolation).\n"
        "  * CONSENT_LINE is the compliance placeholder, so nothing is spoken unprompted.\n"
        "  The wire format itself is covered offline by tests/telephony/test_vobiz_serializer\n"
        "  .py and test_media_auth.py; this script is the belt-and-braces check.",
        flush=True,
    )
    os._exit(2)


def main() -> int:
    threading.Timer(WATCHDOG_SECS, _give_up).start()
    app = build_media_app(
        auto_hang_up=False,
        build_stt_fn=lambda s: _Passthrough(),
        build_vad_fn=lambda s: None,
        build_llm_fn=lambda s: _Passthrough(),
        build_tts_fn=lambda s: _EchoTTS(),
    )
    with TestClient(app) as client:
        # 1. The answer fetch. New with Vobiz — Twilio's TwiML travelled inline in the REST
        #    call, so nothing ever called back into this host over HTTP.
        answer = client.post("/answer", data={"CallUUID": "CA_test"})
        assert answer.status_code == 200, f"/answer returned {answer.status_code}"
        xml = answer.text
        assert 'bidirectional="true"' in xml, "answer XML is not bidirectional — Roma is mute"
        match = re.search(r"wss://[^<\s]+", xml)
        assert match, f"no websocket URL in the answer XML:\n{xml}"
        ws_path = match.group(0).split("localhost:8020", 1)[-1]
        print(f"answer   : {xml.splitlines()[-2].strip()[:96]}…")

        # 2. The socket, carrying the token the answer just minted.
        with client.websocket_connect(ws_path) as ws:
            ws.send_text(
                json.dumps(
                    {
                        "event": "start",
                        "start": {
                            "callId": "CA_test",
                            "streamId": "ST_test",
                            "tracks": ["inbound"],
                            "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000},
                        },
                    }
                )
            )
            payload = base64.b64encode(SILENCE_ULAW).decode()
            for _ in range(N_INBOUND):
                ws.send_text(json.dumps({"event": "media", "media": {"payload": payload}}))

            outbound = 0
            for _ in range(40):
                msg = json.loads(ws.receive_text())
                if msg.get("event") == "playAudio":
                    # Assert the envelope, not just the count: a playAudio without streamId
                    # is silently dropped by Vobiz, which on a live call is indistinguishable
                    # from Roma having nothing to say.
                    assert msg.get("streamId") == "ST_test", f"bad streamId: {msg}"
                    assert msg["media"]["contentType"] == "audio/x-mulaw", f"bad type: {msg}"
                    assert msg["media"]["payload"], "empty payload"
                    outbound += 1
                if outbound >= 5:
                    break

            handles = sole_call(app)
            counter = handles.counter
            print(
                f"audio-IN : counted {counter.frame_count} inbound frames "
                f"({counter.byte_count} PCM bytes)"
            )
            print(f"audio-OUT: received {outbound} well-formed playAudio events")
            ok = counter.frame_count >= N_INBOUND and outbound >= 5
            print("RESULT:", "PASS" if ok else "FAIL")
            sys.stdout.flush()
            os._exit(0 if ok else 1)


if __name__ == "__main__":
    main()
