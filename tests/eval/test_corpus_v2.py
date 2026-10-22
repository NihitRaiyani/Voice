"""Synthetic text/metadata only: no recordings, ASR or new measured results."""
import copy
import json
from pathlib import Path

import pytest


def manifest():
    return {'schema': 'roma-asr-manifest-v2', 'recordings': [{
        'recording_id': 'fake-recording-a', 'speaker_id': 'fake-speaker-a',
        'language_state': 'HINDI_ENGLISH', 'natural_switch': True,
        'switch_positions_ms': [500], 'duration_ms': 1000, 'channel': 'clean',
        'audio_sha256': 'a' * 64, 'reference': 'Amit tomorrow 10 AM Vadodara',
        'entities': [{'type': kind, 'reference': value} for kind, value in
                     [('name', 'Amit'), ('date', 'tomorrow'), ('time', '10 AM'), ('branch', 'Vadodara')]],
        'license': 'SYNTHETIC TEST ONLY',
        'consent': {'granted': True, 'reference': 'fake-consent-a',
                    'permitted_use': 'asr-evaluation', 'storage_policy': 'fake-policy'},
    }]}


def limits():
    from roma.eval.corpus_v2 import CorpusMinimums
    return CorpusMinimums(min_distinct_speakers=1, min_distinct_recordings=1)


def test_v2_accepts_explicit_metadata_and_exposes_a_schema():
    from roma.eval.corpus_v2 import CorpusManifest, validate_corpus
    checked = validate_corpus(manifest(), limits())
    assert checked.recordings[0].natural_switch
    assert 'recordings' in CorpusManifest.model_json_schema()['properties']


@pytest.mark.parametrize('change', ['duplicate-id', 'duplicate-audio', 'entity', 'license', 'consent', 'switch', 'channel'])
def test_v2_rejects_duplicate_or_incomplete_recording_metadata(change):
    from roma.eval.corpus_v2 import validate_corpus
    value = manifest()
    row = value['recordings'][0]
    if change.startswith('duplicate'):
        other = copy.deepcopy(row)
        if change == 'duplicate-audio':
            other['recording_id'] = 'different-id'
        value['recordings'].append(other)
    elif change == 'entity':
        row['entities'][0]['reference'] = 'not in reference'
    elif change in {'license', 'consent'}:
        del row[change]
    elif change == 'switch':
        row['switch_positions_ms'] = []
    else:
        row['channel'] = 'unknown'
    with pytest.raises(ValueError):
        validate_corpus(value, limits())


def test_v2_minimums_are_required_config_and_no_default_counts_are_invented():
    from roma.eval.corpus_v2 import CorpusMinimums, validate_corpus
    defaults = json.loads(Path('configs/asr-corpus-v2.json').read_text())
    assert defaults == {'min_distinct_speakers': None, 'min_distinct_recordings': None}
    with pytest.raises(ValueError, match='configure'):
        validate_corpus(manifest(), CorpusMinimums())
    with pytest.raises(ValueError, match='speakers'):
        validate_corpus(manifest(), CorpusMinimums(min_distinct_speakers=2, min_distinct_recordings=1))
    with pytest.raises(ValueError, match='recordings'):
        validate_corpus(manifest(), CorpusMinimums(min_distinct_speakers=1, min_distinct_recordings=2))
    with pytest.raises(ValueError):
        CorpusMinimums(min_distinct_speakers=True)


def test_entity_recall_uses_unicode_normalization_and_whole_phrases():
    from roma.eval.corpus_v2 import EntityReference, entity_recall
    annotations = [EntityReference(type='name', reference='Amit'),
                   EntityReference(type='time', reference='૧૦ AM'),
                   EntityReference(type='branch', reference='વડોદરા')]
    scored = entity_recall(annotations, 'Amitabh 10 am વડોદરા')
    assert scored['name'] == {'expected': 1, 'matched': 0, 'recall': 0.0}
    assert scored['time']['recall'] == scored['branch']['recall'] == 1.0
    assert scored['date']['recall'] is None


def test_per_speaker_edit_totals_cannot_hide_one_speakers_failures():
    from roma.eval.corpus_v2 import score_corpus, validate_corpus
    value = manifest()
    second = copy.deepcopy(value['recordings'][0])
    second.update(recording_id='fake-recording-b', speaker_id='fake-speaker-b', audio_sha256='b' * 64)
    value['recordings'].append(second)
    checked = validate_corpus(value, limits())
    hypotheses = {'fake-recording-a': value['recordings'][0]['reference'], 'fake-recording-b': ''}
    result = score_corpus(checked, hypotheses)
    speakers = {row['speaker_id']: row for row in result['per_speaker']}
    assert speakers['fake-speaker-a']['wer'] == speakers['fake-speaker-a']['cer'] == 0.0
    assert speakers['fake-speaker-b']['wer'] == speakers['fake-speaker-b']['cer'] == 1.0
    assert result['per_entity']['name'] == {'expected': 2, 'matched': 1, 'recall': 0.5}
    assert value == {**manifest(), 'recordings': [manifest()['recordings'][0], second]}
    with pytest.raises(ValueError, match='recording'):
        score_corpus(checked, {'fake-recording-a': 'incomplete'})


def test_recording_protocol_covers_annotation_and_owner_approval_before_collection():
    protocol = Path('docs/26-asr-recording-protocol.md').read_text()
    for header in ('## Coverage', '## Identity and annotation', '## Consent and storage', '## Before collecting'):
        assert header in protocol
    for field in ('speaker_id', 'recording_id', 'natural_switch', 'switch_positions_ms',
                  'min_distinct_speakers', 'min_distinct_recordings', 'name', 'date', 'time', 'branch'):
        assert field in protocol
    assert 'without a pause' in protocol
    assert 'No audio is recorded or fetched' in protocol
    assert '26-asr-recording-protocol.md' in Path('docs/README.md').read_text()


def test_report_adds_v2_breakdowns_and_leaves_v1_format_unchanged():
    from roma.eval.asr_benchmark import MANIFEST_SCHEMA, add_corpus_breakdowns
    legacy = {'schema': 'roma-asr-benchmark-v1', 'cases': [], 'summaries': []}
    assert add_corpus_breakdowns(legacy, {'schema': MANIFEST_SCHEMA}) is legacy
    value = manifest()
    report = {'schema': 'roma-asr-benchmark-v1', 'cases': [{
        'recording_id': 'fake-recording-a', 'status': 'ok',
        'hypothesis': value['recordings'][0]['reference'],
    }]}
    original = copy.deepcopy(report)
    result = add_corpus_breakdowns(report, value, minimums=limits())
    assert report == original
    assert result['manifest_schema'] == 'roma-asr-manifest-v2'
    assert result['per_speaker'][0]['speaker_id'] == 'fake-speaker-a'
    assert result['per_entity']['time']['recall'] == 1.0


@pytest.mark.parametrize('error', ['duplicate', 'missing', 'wrong-reference', 'wrong-identity'])
def test_report_rejects_incomplete_or_mismatched_recording_identity(error):
    from roma.eval.asr_benchmark import add_corpus_breakdowns
    report = {'cases': [{'recording_id': 'fake-recording-a', 'hypothesis': ''}]}
    if error == 'duplicate':
        report['cases'] *= 2
    elif error == 'missing':
        report['cases'] = []
    elif error == 'wrong-reference':
        report['cases'][0]['reference'] = 'different source reference'
    else:
        report['manifest_sha256'] = '0' * 64
    with pytest.raises(ValueError):
        add_corpus_breakdowns(report, manifest(), minimums=limits())


def test_report_counts_failed_recordings_as_empty_without_hiding_them():
    from roma.eval.asr_benchmark import add_corpus_breakdowns
    report = {'cases': [{'recording_id': 'fake-recording-a', 'status': 'error', 'hypothesis': None}]}
    result = add_corpus_breakdowns(report, manifest(), minimums=limits())
    assert result['cases'][0]['status'] == 'error'
    assert result['per_entity']['name']['recall'] == 0
    assert result['per_speaker'][0]['word_edits'] > 0


def test_offline_report_cli_never_reads_audio_or_loads_models(tmp_path, monkeypatch, capsys):
    import asyncio

    import scripts.asr_benchmark as cli
    monkeypatch.setattr(cli, 'run_benchmark', lambda *a, **kw: pytest.fail('ASR reached'))
    monkeypatch.setattr(cli, 'fetch_public_samples', lambda *a, **kw: pytest.fail('network reached'))
    monkeypatch.setattr('roma.eval.asr_benchmark.read_wav', lambda *a: pytest.fail('audio reached'))
    monkeypatch.setattr(cli, 'WhisperMLXSTT', lambda *a, **kw: pytest.fail('model reached'))
    paths = {key: tmp_path / (key + '.json') for key in ('manifest', 'report', 'config', 'output')}
    paths['manifest'].write_text(json.dumps(manifest()))
    paths['report'].write_text(json.dumps({'cases': [{'recording_id': 'fake-recording-a', 'hypothesis': 'Amit tomorrow 10 AM Vadodara'}]}))
    paths['config'].write_text(json.dumps({'min_distinct_speakers': 1, 'min_distinct_recordings': 1}))
    before = {key: path.read_bytes() for key, path in paths.items() if key != 'output'}
    args = cli.parser().parse_args(['annotate', '--manifest', str(paths['manifest']), '--report', str(paths['report']),
                                   '--corpus-config', str(paths['config']), '--output', str(paths['output'])])
    assert asyncio.run(cli.run(args)) == 0
    result = json.loads(paths['output'].read_text())
    assert result['per_speaker'] and result['per_entity']
    assert 'per_speaker' in capsys.readouterr().out
    assert before == {key: paths[key].read_bytes() for key in before}
    args.output = paths['report']
    with pytest.raises(ValueError, match='overwrite'):
        asyncio.run(cli.run(args))
