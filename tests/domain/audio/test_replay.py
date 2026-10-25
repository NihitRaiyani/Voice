import json
from hashlib import sha256

import pytest
from roma.domain.audio.adaptive_jitter import PacketEnvelope
from roma.domain.audio.pcm import AudioFormatError, pack
from roma.domain.audio.replay import MetadataJournal, ReplayRecord


def test_metadata_roundtrip_is_strict_bounded_and_contains_no_pcm_or_arbitrary_fields():
    pcm = pack([1000] * 320)
    record = ReplayRecord('packet', 0, 0, PacketEnvelope(1, 0, 0, sha256(pcm).hexdigest()))
    journal = MetadataJournal(max_entries=2, max_frames=1, max_payload_bytes=640)
    journal.append(record)
    journal.append(ReplayRecord('flush', 1, 0))
    serialized = json.dumps([item.to_dict() for item in journal.snapshot()])
    restored = MetadataJournal.from_records(json.loads(serialized))
    assert restored.snapshot() == journal.snapshot()
    assert set(record.envelope.to_dict()) == {'seq_no', 'timestamp_ms', 'turn_epoch', 'payload_hash'}
    with pytest.raises(AudioFormatError):
        journal.append(ReplayRecord('drain', 2, 0))
    with pytest.raises(AudioFormatError):
        ReplayRecord.from_dict({**record.to_dict(), 'pcm': pcm})
    with pytest.raises(AudioFormatError):
        ReplayRecord('drain', True, 0)


def test_replay_needs_an_authorized_resolver_and_flushes_observed_endpoint_tail():
    from roma.domain.audio.replay import replay_audio

    payloads = {}
    records = []
    for seq in (1, 2, 3):
        pcm = pack([1000 * seq] * 320)
        envelope = PacketEnvelope(seq, (seq - 1) * 20, 0, sha256(pcm).hexdigest())
        payloads[envelope.payload_hash] = pcm
        records.append(ReplayRecord('packet', (seq - 1) * 20, 0, envelope))
    records.append(ReplayRecord('flush', 41, 0))
    journal = MetadataJournal.from_records(records)
    with pytest.raises(AudioFormatError):
        replay_audio(journal, payload_resolver=None, speech_probability=lambda pcm: 0.9)
    result = replay_audio(journal, payload_resolver=lambda env: payloads[env.payload_hash],
                          speech_probability=lambda pcm: 0.9)
    assert result.pcm == b''.join(payloads[record.envelope.payload_hash] for record in records[:3])
    assert len(result.utterances) == 1
    assert result.utterances[0].pcm.startswith(result.pcm)
    assert result.metrics['tail_padding_bytes'] == 128
    assert replay_audio(journal, payload_resolver=lambda env: payloads[env.payload_hash],
                        speech_probability=lambda pcm: 0.9) == result


@pytest.mark.parametrize('bad_kind', [[], {}, True, None, 1])
def test_control_kind_rejects_non_strings_with_a_sanitized_format_error(bad_kind):
    with pytest.raises(AudioFormatError):
        ReplayRecord(bad_kind, 0, 0)


def test_concealed_waveform_is_labelled_and_excluded_from_endpoint_observations():
    from roma.domain.audio.replay import replay_audio

    payloads = {}
    records = []
    for seq in (1, 2, 4, 5):
        pcm = pack([1000] * 320)
        env = PacketEnvelope(seq, (seq - 1) * 20, 0, sha256(pcm).hexdigest())
        payloads[env.payload_hash] = pcm
        records.append(ReplayRecord('packet', seq, 0, env))
    records.append(ReplayRecord('flush', 10, 0))
    result = replay_audio(MetadataJournal.from_records(records),
                          payload_resolver=lambda env: payloads[env.payload_hash],
                          speech_probability=lambda pcm: 0.9)
    assert result.metrics['concealed_frames'] == 1
    assert result.metrics['concealed_frames_excluded_from_endpoint'] == 1
    assert result.metrics['endpoint_observed_pcm_bytes'] == 4 * 640
    assert len(result.pcm) == 5 * 640
    assert [frame.seq_no for frame in result.playout if frame.concealed] == [3]


def test_failed_authorized_payload_lookup_is_sanitized_and_cannot_replay_metadata_alone():
    from roma.domain.audio.replay import replay_audio

    envelope = PacketEnvelope(1, 0, 0, '0' * 64)
    journal = MetadataJournal.from_records([ReplayRecord('packet', 0, 0, envelope)])
    def unavailable(_envelope):
        raise RuntimeError('private path must never escape')
    with pytest.raises(AudioFormatError, match='unavailable'):
        replay_audio(journal, payload_resolver=unavailable, speech_probability=lambda pcm: 0.9)


def test_epoch_controls_and_full_call_capacity_overrides_are_explicit():
    journal = MetadataJournal(max_entries=50000, max_frames=15000, max_payload_bytes=9600000)
    journal.append(ReplayRecord('epoch', 0, 1))
    with pytest.raises(AudioFormatError):
        journal.append(ReplayRecord('epoch', 1, 1))
    with pytest.raises(AudioFormatError):
        journal.append(ReplayRecord('drain', 1, 0))
    assert len(journal.snapshot()) == 1
    with pytest.raises(AudioFormatError):
        MetadataJournal(max_frames=True)
    with pytest.raises(AudioFormatError):
        MetadataJournal(max_payload_bytes=9600001)


def test_epoch_control_discards_pending_old_pcm_and_rejects_late_old_packets():
    from roma.domain.audio.replay import replay_audio

    old, new = pack([1000] * 320), pack([3000] * 320)
    old_envelope = PacketEnvelope(1, 0, 0, sha256(old).hexdigest())
    new_envelope = PacketEnvelope(1, 0, 1, sha256(new).hexdigest())
    records = [ReplayRecord('packet', 0, 0, old_envelope), ReplayRecord('epoch', 10, 1),
               ReplayRecord('packet', 20, 0, old_envelope),
               ReplayRecord('packet', 21, 1, new_envelope), ReplayRecord('flush', 30, 1)]
    payloads = {old_envelope.payload_hash: old, new_envelope.payload_hash: new}
    result = replay_audio(MetadataJournal.from_records(records),
                          payload_resolver=lambda env: payloads[env.payload_hash],
                          speech_probability=lambda pcm: 0.9)
    assert result.pcm == new
    assert result.metrics['stale_epoch'] == 1 and result.metrics['discarded'] == 1


@pytest.mark.parametrize('limits', [
    {'max_entries': 8, 'max_frames': 1, 'max_payload_bytes': 1280},
    {'max_entries': 8, 'max_frames': 2, 'max_payload_bytes': 640},
])
def test_journal_frame_and_byte_budgets_fail_without_appending_partial_records(limits):
    envelope = PacketEnvelope(1, 0, 0, '0' * 64)
    record = ReplayRecord('packet', 0, 0, envelope)
    journal = MetadataJournal(**limits)
    journal.append(record)
    with pytest.raises(AudioFormatError):
        journal.append(record)
    assert journal.snapshot() == (record,)


def test_replay_output_loss_span_is_bounded_even_with_only_two_resolved_frames():
    from roma.domain.audio.replay import replay_audio

    pcm = pack([1000] * 320)
    digest = sha256(pcm).hexdigest()
    records = [ReplayRecord('packet', 0, 0, PacketEnvelope(1, 0, 0, digest)),
               ReplayRecord('packet', 1, 0, PacketEnvelope(100, 1980, 0, digest)),
               ReplayRecord('flush', 2, 0)]
    journal = MetadataJournal.from_records(records, max_frames=2)
    with pytest.raises(AudioFormatError, match='frame budget'):
        replay_audio(journal, payload_resolver=lambda env: pcm,
                     speech_probability=lambda pcm: 0.9)
