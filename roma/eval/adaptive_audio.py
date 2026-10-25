"""Deterministic synthetic Phase 5A lab; no settings, inference, devices or SQL."""

import math
import random
from datetime import UTC, datetime
from hashlib import sha256

from roma.core.ai_contracts import AudioFormat, STTRequest
from roma.domain.audio.adaptive_jitter import AdaptiveJitterBuffer, PacketEnvelope, integer
from roma.domain.audio.g711 import encode
from roma.domain.audio.jitter import AudioPacket, FixedJitterBuffer
from roma.domain.audio.pcm import pack, unpack
from roma.domain.audio.replay import MetadataJournal, ReplayRecord, replay_audio
from roma.domain.conversation.state import CallState
from roma.domain.conversation.turn import advance_turn
from roma.eval.speech import ScriptedSTT


def fixture(*, seed=3550):
    """Ten-second tone/silence fixture; exactly 5% source frames are dropped."""
    integer(seed, maximum=2**32 - 1)
    randomizer = random.Random(seed)
    loss = set(randomizer.sample(range(2, 500), 25))
    payloads, arrivals = {}, []
    for seq in range(1, 501):
        samples = [round(6000 * math.sin(2 * math.pi * 220 * ((seq - 1) * 320 + i) / 16000))
                   if seq <= 240 else 0 for i in range(320)]
        pcm = pack(samples)
        envelope = PacketEnvelope(seq, (seq - 1) * 20, 0, sha256(pcm).hexdigest())
        payloads[envelope.payload_hash] = pcm
        delay = 50 + randomizer.randint(-50, 50)
        if seq not in loss:
            arrivals.append(ReplayRecord("packet", envelope.timestamp_ms + delay, 0, envelope))
    records = [ReplayRecord("drain", tick, 0) for tick in range(0, 10251, 5)]
    records.extend(arrivals)
    records.sort(key=lambda record: (record.at_ms, record.kind == "packet"))
    records.append(ReplayRecord("flush", 10260, 0, last_seq_no=500))
    return MetadataJournal.from_records(records), payloads


def fixed_baseline(journal, payloads):
    """Existing unchanged fixed60ms G.711 path on the same packet schedule.

    This compares transport counters, not signal fidelity between codecs.
    """
    buffer = FixedJitterBuffer()
    emitted = []
    for record in journal.snapshot():
        if record.kind == "packet":
            env = record.envelope
            mu_law = encode(pack(unpack(payloads[env.payload_hash])[::2]))
            buffer.push(AudioPacket(env.seq_no, env.timestamp_ms, mu_law), now_ms=record.at_ms)
        emitted.extend(buffer.drain(now_ms=record.at_ms))
    return {**buffer.metrics.snapshot(), "delay_ms": 60, "plc_enabled": False,
            "pending_packets": buffer.pending, "discontinuities": sum(item.payload is None for item in emitted)}


def recovery_probes():
    """Execute overflow recovery and a four-frame loss burst, not hardcoded claims."""
    now = 0.0
    pcm = pack([1000] * 320)
    def packet(seq):
        return PacketEnvelope(seq, (seq - 1) * 20, 0, sha256(pcm).hexdigest()), pcm
    buffer = AdaptiveJitterBuffer(clock=lambda: now, max_packets=3)
    for seq in (1, 2, 3):
        assert buffer.push(*packet(seq))
    assert not buffer.push(*packet(4))
    now = 120
    assert len(buffer.drain()) == 3
    assert buffer.push(*packet(4))
    assert buffer.flush()[0].seq_no == 4
    overflow = {"rejected": buffer.metrics.overflow, "recovered": buffer.pending == 0}
    now = 0.0
    burst = AdaptiveJitterBuffer(clock=lambda: now)
    burst.push(*packet(1))
    burst.push(*packet(6))
    output = burst.flush()
    return {"overflow": overflow, "four_loss_burst": {"gap_frames": burst.metrics.gap_frames,
            "concealed_frames": sum(frame.concealed for frame in output),
            "discontinuities": sum(frame.pcm is None for frame in output)}}


async def report(*, seed=3550):
    journal, payloads = fixture(seed=seed)
    def resolver(envelope):
        return payloads[envelope.payload_hash]  # Only this generated synthetic fixture is authorized.
    def probability(pcm):
        return 0.9 if any(unpack(pcm)) else 0.0  # Scripted score, not a model/VAD measurement.
    first = replay_audio(journal, payload_resolver=resolver, speech_probability=probability)
    second = replay_audio(journal, payload_resolver=resolver, speech_probability=probability)
    assert first == second
    # Run the actual software controller's deterministic path, with no model,
    # calendar, external sink or booking injection. A stop is terminal input.
    state = CallState(call_sid="synthetic-adaptive-replay")
    stt = ScriptedSTT(("Please stop calling",))
    for utterance in first.utterances:
        if not utterance.pcm:
            continue
        transcript = await stt.transcribe(STTRequest(utterance.pcm, AudioFormat(16000), "en"))
        await advance_turn(state, transcript.text, now=datetime(2026, 1, 1, tzinfo=UTC),
                           controller_only=True)
        if state.lead_wants_out:
            break
    return {"schema": "phase5a-adaptive-replay-v1", "synthetic_only": True,
            "native_quality_measured": False, "inference_calls": False, "database_access": False,
            "impairment": {"seed": seed, "jitter_radius_ms": 50, "loss_percent": 5,
                           "source_frames": 500, "dropped_frames": 25},
            "adaptive": {**first.metrics, "pcm_sha256": sha256(first.pcm).hexdigest(),
                         "endpoint_utterances": len(first.utterances)},
            "fixed_baseline": fixed_baseline(journal, payloads),
            "recovery_probes": recovery_probes(), "repeat_bit_identical": first == second,
            "controller": {"scripted_stt_calls": stt.calls, "turns": state.turn_count,
                           "stop_signal_honoured": state.lead_wants_out,
                           "booking_effects": int(state.appointment_id is not None)},
            "limits": {"target_delay_ms": [40, 120], "max_consecutive_plc": 3,
                       "plc_excluded_from_endpoint": True,
                       "probability_and_transcripts_scripted": True,
                       "tail_padding_is_synthetic": True,
                       "receiver_wait_and_timer_lateness_reported_separately": True,
                       "production_path_changed": False}}
