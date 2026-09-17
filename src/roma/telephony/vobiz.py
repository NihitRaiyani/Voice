"""Vobiz WebSocket `<Stream>` frame serializer (docs.vobiz.ai/concepts/streaming-websockets).

Vobiz carries the SIP leg to the PSTN itself and forks the call audio to us over a
WebSocket. That is the transport pipecat is built for, and it is why this integration needs
no SIP stack: **Vobiz is the SIP client, not Roma.** The trunk credentials
(`28acf939.sip.vobiz.ai`, `sarvam.weltec`, …) authenticate Vobiz's own outbound trunk and are
configured account-side; nothing on this path consumes them. See `docs/decisions.md`.

## Lineage

This is modelled on pipecat's `PlivoFrameSerializer` and is close to a copy of it, because
Vobiz's wire format *is* Plivo's for the parts they share — `playAudio`, `clearAudio`,
`streamId`, base64 μ-law in `media.payload`, and a REST API at
`/api/v1/Account/{auth_id}/Call/`. It is a separate class rather than a subclass for three
reasons, each of which is a real difference and not a stylistic one:

1. **`start.mediaFormat` is honoured.** Vobiz declares the inbound encoding and sample rate
   per connection and supports `audio/x-l16` at 8k/16k as well as `audio/x-mulaw` at 8k.
   Plivo's serializer hardcodes μ-law and never reads `start`. Assuming the format when the
   carrier has just told you what it is produces silence or noise, and it is the kind of
   failure that looks like a model problem for an hour before anyone checks the codec.
2. **`checkpoint` / `playedStream`** exist in Vobiz's protocol and not Plivo's.
3. **Auth is `X-Auth-ID`/`X-Auth-Token` headers**, not HTTP basic.

Subclassing would have meant overriding `deserialize`, `serialize` and `_hang_up_call` while
depending on pipecat's private attribute names across versions — more coupling for less code.

## Hang-up

There is deliberately no REST hang-up here, and `auto_hang_up` defaults to False.

`<Stream>` is emitted WITHOUT `keepCallAlive`, so when this socket closes the call ends —
the carrier does the teardown and there is no second API surface to get wrong. Pipecat's
Twilio serializer hides a REST call inside what looks like a codec (`auto_hang_up=True` by
default), which is a surprise worth not re-inheriting. The path is kept behind the flag for
the case where a future `keepCallAlive="true"` flow needs it, and the endpoint shape follows
Plivo's because the rest of the API does — but it is **unverified against Vobiz's docs**, so
it stays off until someone confirms it.
"""

import base64
import json
import logging

from pipecat.audio.utils import create_stream_resampler, pcm_to_ulaw, ulaw_to_pcm
from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    Frame,
    InputAudioRawFrame,
    InterruptionFrame,
    OutputAudioRawFrame,
    OutputTransportMessageFrame,
    OutputTransportMessageUrgentFrame,
    StartFrame,
)
from pipecat.serializers.base_serializer import FrameSerializer

_log = logging.getLogger("roma.telephony")

MULAW = "audio/x-mulaw"
L16 = "audio/x-l16"

# What Vobiz sends if `start` carries no `mediaFormat`. Matches the `contentType` the
# `<Stream>` element asks for (see `telephony/answer.py`) and the rate the whole audio path
# already runs at — the cached `.ulaw` assets, the recorder, and the VAD.
DEFAULT_ENCODING = MULAW
DEFAULT_SAMPLE_RATE = 8000


class VobizFrameSerializer(FrameSerializer):
    """Translate between Pipecat frames and Vobiz's `<Stream>` WebSocket protocol."""

    class InputParams(FrameSerializer.InputParams):
        """Configuration for `VobizFrameSerializer`.

        Parameters:
            vobiz_sample_rate: Rate for audio sent TO Vobiz. 8000 Hz for μ-law.
            sample_rate: Optional override for the pipeline input rate.
            auto_hang_up: REST hang-up on EndFrame. Off by default — see the module
                docstring; the stream closing already ends the call.
        """

        vobiz_sample_rate: int = DEFAULT_SAMPLE_RATE
        sample_rate: int | None = None
        auto_hang_up: bool = False

    def __init__(
        self,
        stream_id: str,
        call_id: str | None = None,
        auth_id: str | None = None,
        auth_token: str | None = None,
        params: "InputParams | None" = None,
    ) -> None:
        params = params or VobizFrameSerializer.InputParams()
        super().__init__(params)
        self._params: VobizFrameSerializer.InputParams = params

        if self._params.auto_hang_up:
            missing = [
                name
                for name, value in (
                    ("call_id", call_id),
                    ("auth_id", auth_id),
                    ("auth_token", auth_token),
                )
                if not value
            ]
            if missing:
                # Degrade, do not raise. This object is constructed PER CALL from the `/ws`
                # handler, so raising here does not "fail fast" in any useful sense — it
                # drops a call that was otherwise fine, with a lead already on the line, over
                # a feature that call did not need. The REST hang-up is an optional extra:
                # `<Stream>` omits `keepCallAlive`, so the call already ends when the socket
                # closes. Losing it costs nothing observable.
                _log.warning(
                    "vobiz: auto_hang_up requested but %s unavailable — REST hang-up is "
                    "DISABLED for this call. The call still ends when the stream closes.",
                    ", ".join(missing),
                )
                self._params = self._params.model_copy(update={"auto_hang_up": False})

        self._stream_id = stream_id
        self._call_id = call_id
        self._auth_id = auth_id
        self._auth_token = auth_token

        # What Vobiz said it is SENDING us. Overwritten from `start.mediaFormat` by
        # `set_media_format` before any media arrives.
        self._in_encoding = DEFAULT_ENCODING
        self._in_sample_rate = DEFAULT_SAMPLE_RATE

        self._sample_rate = 0  # pipeline input rate, set in `setup`
        self._input_resampler = create_stream_resampler(
            clear_after_secs=self._params.resampler_clear_after_secs
        )
        self._output_resampler = create_stream_resampler(
            clear_after_secs=self._params.resampler_clear_after_secs
        )
        self._hangup_attempted = False

        # Proof of audio-OUT, counted at the LAST point before the socket write.
        #
        # `InboundAudioCounter` has answered "did the caller reach us?" since Step 1, and the
        # teardown line carries `inbound_frames`. There was no counterpart, so "Roma went
        # silent" could not be told apart from "Roma spoke and Vobiz dropped it" — the two
        # have completely different fixes and the log was equally consistent with both.
        # A processor could not answer it either: it sees frames, not wire bytes. Only here,
        # after `pcm_to_ulaw` and inside the branch that actually returns a `playAudio`, is
        # the count a statement about what Vobiz received.
        self.play_frames = 0
        self.play_bytes = 0
        self.clear_events = 0
        self._logged_first_play = False

    def set_media_format(self, encoding: "str | None", sample_rate: "int | None") -> None:
        """Adopt the inbound format Vobiz declared in its `start` event.

        Called by `media.py` from the prelude parser, before the pipeline starts, so the
        first `media` frame is already decoded correctly. An unknown encoding falls back to
        the configured default and says so — guessing quietly is how a codec mismatch turns
        into an hour of debugging the wrong layer.
        """
        if encoding:
            # Vobiz may send a parameterised content type ("audio/x-l16;rate=16000").
            base = encoding.split(";")[0].strip().casefold()
            if base in (MULAW, L16):
                self._in_encoding = base
            else:
                _log.warning(
                    "vobiz: unknown inbound encoding %r; decoding as %s",
                    encoding,
                    self._in_encoding,
                )
        if sample_rate:
            self._in_sample_rate = int(sample_rate)
        _log.info(
            "vobiz stream media format: inbound %s @ %dHz, outbound %s @ %dHz",
            self._in_encoding,
            self._in_sample_rate,
            MULAW,
            self._params.vobiz_sample_rate,
        )

    async def setup(self, frame: StartFrame) -> None:
        """Learn the pipeline's input rate from the StartFrame."""
        self._sample_rate = self._params.sample_rate or frame.audio_in_sample_rate

    async def serialize(self, frame: Frame) -> "str | bytes | None":
        """Pipecat frame -> a Vobiz WebSocket command, or None when nothing should go out."""
        if (
            self._params.auto_hang_up
            and not self._hangup_attempted
            and isinstance(frame, (EndFrame, CancelFrame))
        ):
            self._hangup_attempted = True
            await self._hang_up_call()
            return None

        if isinstance(frame, InterruptionFrame):
            # THE barge-in path. The lead has started talking and everything Roma has queued
            # at the carrier has to go, not just what has not been generated yet. Roma's
            # whole turn-taking design assumes this flush actually lands: if it silently did
            # not, she would keep talking over the lead for however much audio Vobiz still
            # held, which is exactly the failure barge-in exists to prevent.
            self.clear_events += 1
            # INFO, not DEBUG, and deliberately noisy. Barge-in has now failed on three
            # calls with the PIPELINE reporting success every time — "barge-in: user speech
            # exceeded 0.60s", then `broadcasting interruption` 8ms later, then Sarvam
            # disconnected — while the lead kept hearing Roma. Everything upstream of the
            # carrier is provably working, so the remaining question is whether this flush
            # reaches Vobiz and whether Vobiz honours it. Without a timestamped line for the
            # send there is nothing to compare against the moment her voice actually stops.
            _log.info(
                "clearAudio sent to Vobiz (#%d, %d audio frames written so far)",
                self.clear_events,
                self.play_frames,
            )
            return json.dumps({"event": "clearAudio", "streamId": self._stream_id})

        # OutputAudioRawFrame, NOT the AudioRawFrame base class.
        #
        # `InputAudioRawFrame` — the LEAD'S OWN audio, arriving from the carrier — is also an
        # `AudioRawFrame`. Matching the base class meant any inbound frame that reached this
        # serializer was re-encoded and written straight back to Vobiz as `playAudio`: a
        # literal loopback of the caller into their own ear.
        #
        # This is the "echo" reported on every single live call and dismissed three times,
        # including by an earlier check of mine that tested whether ROMA heard herself — the
        # opposite direction — and concluded there was no echo. On call 172f24c5 the lead
        # asked outright: "मुझे मेरी आवाजें क्यों सुनाई दे रही हैं" ("why am I hearing my own
        # voice?"). That is the bug, in the lead's own words.
        #
        # Narrowing loses nothing Roma says: `TTSAudioRawFrame` subclasses
        # `OutputAudioRawFrame`, and the filler/holding/canned clips are pushed as
        # `OutputAudioRawFrame` already.
        if isinstance(frame, OutputAudioRawFrame):
            payload = await pcm_to_ulaw(
                frame.audio,
                frame.sample_rate,
                self._params.vobiz_sample_rate,
                self._output_resampler,
            )
            if not payload:
                return None  # nothing to say; not an error
            self.play_frames += 1
            self.play_bytes += len(payload)
            if not self._logged_first_play:
                # One line, once per call, at INFO. The teardown totals arrive only after the
                # call is over, which is too late to tell someone still holding the phone
                # whether to keep talking. This says audio-OUT is live while it is live.
                self._logged_first_play = True
                _log.info(
                    "vobiz audio-OUT live: first playAudio written (%d bytes %s @ %dHz)",
                    len(payload),
                    MULAW,
                    self._params.vobiz_sample_rate,
                )
            return json.dumps(
                {
                    "event": "playAudio",
                    "streamId": self._stream_id,
                    "media": {
                        "contentType": MULAW,
                        "sampleRate": self._params.vobiz_sample_rate,
                        "payload": base64.b64encode(payload).decode("utf-8"),
                    },
                }
            )

        if isinstance(frame, (OutputTransportMessageFrame, OutputTransportMessageUrgentFrame)):
            if self.should_ignore_frame(frame):
                return None
            return json.dumps(frame.message)

        return None

    async def deserialize(self, data: "str | bytes") -> "Frame | None":
        """A Vobiz WebSocket event -> a Pipecat frame, or None when it carries no audio."""
        try:
            message = json.loads(data)
        except (ValueError, TypeError):
            _log.warning("vobiz: could not parse a websocket message")
            return None

        event = message.get("event")

        if event == "media":
            payload_b64 = (message.get("media") or {}).get("payload")
            if not payload_b64:
                return None
            try:
                payload = base64.b64decode(payload_b64)
            except (ValueError, TypeError):
                _log.warning("vobiz: media payload was not valid base64")
                return None

            if self._in_encoding == MULAW:
                pcm = await ulaw_to_pcm(
                    payload, self._in_sample_rate, self._sample_rate, self._input_resampler
                )
            else:  # L16 is already PCM16; it only needs the rate matching.
                pcm = await self._input_resampler.resample(
                    payload, self._in_sample_rate, self._sample_rate
                )
            if not pcm:
                return None
            return InputAudioRawFrame(audio=pcm, num_channels=1, sample_rate=self._sample_rate)

        if event in ("playedStream", "clearedAudio"):
            # Acknowledgements for `checkpoint` and `clearAudio`. Nothing downstream waits on
            # them today — Roma's turn-taking runs off its own VAD and TTS frames — so they
            # are logged and dropped rather than turned into frames nothing consumes.
            _log.debug("vobiz: %s", event)
            return None

        return None

    async def _hang_up_call(self) -> None:
        """End the call over REST. Unverified against Vobiz's docs — see the module docstring.

        Never raises: a failed hang-up must not take down a teardown path.
        """
        try:
            import aiohttp

            endpoint = (
                f"https://api.vobiz.ai/api/v1/Account/{self._auth_id}/Call/{self._call_id}/"
            )
            headers = {"X-Auth-ID": self._auth_id or "", "X-Auth-Token": self._auth_token or ""}
            async with (
                aiohttp.ClientSession() as session,
                session.delete(endpoint, headers=headers) as response,
            ):
                if response.status in (200, 202, 204, 404):
                    _log.debug("vobiz: call terminated (status %s)", response.status)
                else:
                    _log.error("vobiz: hang-up failed with status %s", response.status)
        except Exception:  # noqa: BLE001 — teardown must survive a dead API
            _log.exception("vobiz: hang-up request failed")


__all__ = ["VobizFrameSerializer", "MULAW", "L16", "DEFAULT_SAMPLE_RATE"]
