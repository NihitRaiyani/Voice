"""Bounded lab-only metadata/control replay; payload identities are not audio.

No raw PCM, transcript, provider options or caller metadata belongs in this
journal. Audio replay requires a separately authorized, hash-checked resolver.
"""

import math
from dataclasses import dataclass
from hashlib import sha256
from numbers import Real

from roma.domain.audio.adaptive_jitter import FRAME_BYTES, PacketEnvelope, integer
from roma.domain.audio.pcm import AudioFormatError


@dataclass(frozen=True)
class ReplayRecord:
    kind: str
    at_ms: float
    turn_epoch: int
    envelope: PacketEnvelope | None = None
    last_seq_no: int | None = None

    def __post_init__(self):
        if not isinstance(self.kind, str) or self.kind not in {"packet", "drain", "flush", "epoch"}:
            raise AudioFormatError("Unknown replay control")
        if isinstance(self.at_ms, bool) or not isinstance(self.at_ms, Real) or not math.isfinite(self.at_ms) or not 0 <= self.at_ms <= 86400000:
            raise AudioFormatError("Replay time must be finite bounded milliseconds")
        object.__setattr__(self, "at_ms", float(self.at_ms))
        integer(self.turn_epoch)
        if self.kind == "packet":
            if not isinstance(self.envelope, PacketEnvelope) or self.envelope.turn_epoch != self.turn_epoch:
                raise AudioFormatError("A packet requires a matching envelope")
        elif self.envelope is not None:
            raise AudioFormatError("Control records cannot contain a packet")
        if self.last_seq_no is not None:
            integer(self.last_seq_no)
            if self.kind != "flush":
                raise AudioFormatError("Only final input may declare its last sequence")

    def to_dict(self):
        return {"kind": self.kind, "at_ms": self.at_ms, "turn_epoch": self.turn_epoch,
                "envelope": self.envelope.to_dict() if self.envelope is not None else None,
                "last_seq_no": self.last_seq_no}

    @classmethod
    def from_dict(cls, value):
        keys = {"kind", "at_ms", "turn_epoch", "envelope", "last_seq_no"}
        if type(value) is not dict or set(value) != keys:
            raise AudioFormatError("Invalid replay record fields")
        data = dict(value)
        if data["envelope"] is not None:
            data["envelope"] = PacketEnvelope.from_dict(data["envelope"])
        return cls(**data)


class MetadataJournal:
    """Fail explicitly at capacity; never silently trim replay/control history.

    The defaults are a short laboratory run. An explicitly configured five-minute
    run may use up to 100,000 records / 15,000 frames / 9.6 MB resolved PCM.
    """

    def __init__(self, *, max_entries=4096, max_frames=3000, max_payload_bytes=1920000):
        self.max_entries = integer(max_entries, minimum=1, maximum=100000)
        self.max_frames = integer(max_frames, minimum=1, maximum=15000)
        self.max_payload_bytes = integer(max_payload_bytes, minimum=FRAME_BYTES, maximum=9600000)
        self._records: list[ReplayRecord] = []
        self._frames = 0
        self._last_time = 0.0
        self._epoch = 0

    def append(self, record):
        if not isinstance(record, ReplayRecord):
            raise AudioFormatError("Expected a validated replay record")
        frames = self._frames + (record.kind == "packet")
        if len(self._records) >= self.max_entries or frames > self.max_frames or frames * FRAME_BYTES > self.max_payload_bytes:
            raise AudioFormatError("Replay journal capacity exceeded")
        if record.at_ms < self._last_time:
            raise AudioFormatError("Replay time must be monotonic")
        if record.kind == "epoch":
            if record.turn_epoch <= self._epoch:
                raise AudioFormatError("Epoch controls must increase")
        elif record.turn_epoch > self._epoch or (record.kind != "packet" and record.turn_epoch != self._epoch):
            raise AudioFormatError("Replay control epoch does not match")
        self._records.append(record)
        self._frames, self._last_time = frames, record.at_ms
        if record.kind == "epoch":
            self._epoch = record.turn_epoch

    def snapshot(self):
        return tuple(self._records)

    @classmethod
    def from_records(cls, records, **limits):
        journal = cls(**limits)
        for record in records:
            journal.append(record if isinstance(record, ReplayRecord) else ReplayRecord.from_dict(record))
        return journal


@dataclass(frozen=True)
class ReplayResult:
    pcm: bytes
    utterances: tuple
    playout: tuple
    metrics: dict


def replay_audio(journal, *, payload_resolver, speech_probability):
    """Reproduce lab audio/endpoint decisions with no ASR, LLM or booking effects.

    The resolver is an explicit authorization boundary, not a hash inversion.
    Concealed PCM is retained in labelled playout for waveform inspection, but
    never fed to the endpoint as observed speech. Partial real tails are padded
    explicitly; padding is counted and is not accuracy evidence.
    """
    from roma.domain.audio.adaptive_jitter import AdaptiveJitterBuffer
    from roma.domain.audio.endpointing import FRAME_BYTES as ENDPOINT_BYTES
    from roma.domain.audio.endpointing import EndpointDetector

    if not isinstance(journal, MetadataJournal) or not callable(payload_resolver) or not callable(speech_probability):
        raise AudioFormatError("Replay requires a journal and authorized payload/scoring callbacks")
    now = 0.0
    buffer = AdaptiveJitterBuffer(clock=lambda: now)
    endpoint = EndpointDetector()
    carry = bytearray()
    utterances, playout, audio = [], [], []
    counts = {"tail_padding_bytes": 0, "concealed_frames_excluded_from_endpoint": 0,
              "epoch_discarded_pending_bytes": 0, "resolved_payload_bytes": 0,
              "endpoint_observed_pcm_bytes": 0, "output_frame_span": 0, "output_pcm_bytes": 0}

    def feed_real(pcm):
        carry.extend(pcm)
        while len(carry) >= ENDPOINT_BYTES:
            frame = bytes(carry[:ENDPOINT_BYTES])
            del carry[:ENDPOINT_BYTES]
            utterances.extend(endpoint.feed(frame, speech_probability(frame)))

    def tail():
        if carry:
            counts["tail_padding_bytes"] += ENDPOINT_BYTES - len(carry)
            frame = bytes(carry).ljust(ENDPOINT_BYTES, b"\x00")
            carry.clear()
            utterances.extend(endpoint.feed(frame, speech_probability(frame)))

    def consume(frames):
        for frame in frames:
            span = frame.missing_frames if frame.pcm is None else 1
            counts["output_frame_span"] += span
            if counts["output_frame_span"] > journal.max_frames:
                raise AudioFormatError("Replay output frame budget exceeded")
            playout.append(frame)
            if frame.pcm is not None:
                audio.append(frame.pcm)
                counts["output_pcm_bytes"] += len(frame.pcm)
                if counts["output_pcm_bytes"] > journal.max_payload_bytes:
                    raise AudioFormatError("Replay output byte budget exceeded")
            if frame.concealed or frame.pcm is None:
                tail()
                utterances.extend(endpoint.discontinuity())
                counts["concealed_frames_excluded_from_endpoint"] += int(frame.concealed)
            else:
                counts["endpoint_observed_pcm_bytes"] += len(frame.pcm)
                feed_real(frame.pcm)

    for record in journal.snapshot():
        now = record.at_ms
        if record.kind == "packet":
            try:
                payload = payload_resolver(record.envelope)
            except Exception:  # noqa: BLE001 — resolver errors can expose private paths
                raise AudioFormatError("Authorized replay payload is unavailable") from None
            if not isinstance(payload, bytes) or len(payload) != FRAME_BYTES:
                raise AudioFormatError("Resolver did not return one normalized PCM frame")
            if sha256(payload).hexdigest() != record.envelope.payload_hash:
                raise AudioFormatError("Resolved payload does not match its identity")
            counts["resolved_payload_bytes"] += len(payload)
            if counts["resolved_payload_bytes"] > journal.max_payload_bytes:
                raise AudioFormatError("Resolved payload budget exceeded")
            buffer.push(record.envelope, payload)
            consume(buffer.drain())
        elif record.kind == "drain":
            consume(buffer.drain())
        elif record.kind == "flush":
            consume(buffer.flush(last_seq_no=record.last_seq_no))
            tail()
            utterances.extend(endpoint.flush())
        else:
            buffer.advance_epoch(record.turn_epoch)
            counts["epoch_discarded_pending_bytes"] += len(carry) + endpoint.buffered_bytes
            carry.clear()
            endpoint.reset()
    metrics = {**buffer.metrics.snapshot(), **counts, "jitter_ms": buffer.jitter_ms,
               "target_delay_ms": buffer.target_delay_ms, "pending_packets": buffer.pending}
    return ReplayResult(b"".join(audio), tuple(utterances), tuple(playout), metrics)
