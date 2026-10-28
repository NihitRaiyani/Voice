"""Local integrity evidence never substitutes for acoustic or human approval."""

import json
import stat

from roma.services.live_acceptance_evidence import EvidenceStore

COMMIT = "a" * 40


def test_signed_evidence_round_trip_uses_private_separate_key(tmp_path):
    root = tmp_path / "private"
    store = EvidenceStore(root)
    path = store.write("session", {"source_commit": COMMIT, "turn_count": 3})
    verified = store.read("session")
    assert verified["payload"] == {"turn_count": 3}
    assert verified["source_commit"] == COMMIT
    assert verified["schema_version"] == 1
    assert verified["recorded_at"].endswith("Z")
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE((root / ".key").stat().st_mode) == 0o600
    assert len((root / ".key").read_bytes()) == 32
    assert json.loads(path.read_text())["hmac_sha256"] == verified["hmac_sha256"]


def test_store_rejects_symlinks_and_insecure_existing_keys(tmp_path):
    import os

    import pytest
    root = tmp_path / 'private'
    root.mkdir(mode=0o700)
    target = tmp_path / 'target'
    target.write_bytes(b'x' * 32)
    (root / '.key').symlink_to(target)
    with pytest.raises((ValueError, OSError)):
        EvidenceStore(root)
    (root / '.key').unlink()
    (root / '.key').write_bytes(b'x' * 32)
    os.chmod(root / '.key', 0o644)
    with pytest.raises((ValueError, OSError)):
        EvidenceStore(root)


def test_signed_payload_rejects_tampering_and_path_traversal(tmp_path):
    import pytest
    store = EvidenceStore(tmp_path / 'private')
    with pytest.raises(ValueError):
        store.write('../escape', {'source_commit': COMMIT})
    path = store.write('session', {'source_commit': COMMIT, 'turn_count': 3})
    value = json.loads(path.read_text())
    value['payload']['turn_count'] = 99
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        store.read('session')


def test_human_rating_is_required_and_invalid_values_cannot_be_signed(tmp_path):
    import pytest
    store = EvidenceStore(tmp_path / 'private')
    base = dict(source_commit=COMMIT, run_id='test-run', listener='Owner', language='gu',
                notes='Voice sounds clear')
    for score in (None, True, 0, 5.1, float('nan'), 'recommended'):
        with pytest.raises(ValueError):
            store.write('voice_rating', {**base, 'naturalness': score})
    store.write('voice_rating', {**base, 'naturalness': 2.5})
    assert store.read('voice_rating')['payload']['naturalness'] == 2.5


def test_governance_placeholders_and_provider_preview_cannot_close_privacy():
    import pytest
    from roma.services.live_acceptance_evidence import validate_governance
    with pytest.raises(ValueError):
        validate_governance({'approver': 'TBD', 'audio_retention_days': 30})
    with pytest.raises(ValueError):
        validate_governance({'approver': 'recommended'})


def test_readiness_uses_verified_matching_receipts_and_preserves_history(tmp_path):
    from roma.services.live_acceptance_evidence import update_readiness
    repo = tmp_path / 'repo'
    levels = repo / 'docs/levels'
    levels.mkdir(parents=True)
    for name in ('level-06.md', 'level-07.md', 'level-08.md'):
        (levels / name).write_text('Historical naturalness: 2.5/5.\n- [ ] Full gate\n')
    store = EvidenceStore(tmp_path / 'private')
    store.write('session', dict(source_commit=COMMIT, run_id='r1', status='completed',
                               source_tree_sha256='b' * 64))
    store.write('physical', dict(source_commit=COMMIT, run_id='r1', events=[]))
    store.write('voice_rating', dict(source_commit=COMMIT, run_id='r1', listener='Owner',
                                    language='gu', naturalness=4, notes='Improved'))
    update_readiness(repo, store)
    update_readiness(repo, store)
    text = (levels / 'level-06.md').read_text()
    assert '2.5/5' in text and '4/5' in text and '- [ ] Full gate' in text
    assert text.count('BEGIN CURRENT LIVE ACCEPTANCE') == 1
    assert COMMIT in text
    assert 'Acoustic' in (levels / 'level-07.md').read_text()


def test_confirmed_governance_projects_provider_metadata_and_rejects_preview(tmp_path):
    import pytest
    from roma.services.live_acceptance_evidence import validate_governance
    payload = {'approver': 'R Shah', 'approved_at': '2020-01-01T00:00:00Z',
               'policy_reference': 'ApprovalRecordA1', 'audio_retention_days': 0,
               'transcript_retention_days': 90,
               'anthropic': {'organization_reference': 'org-record-1', 'model': 'claude-haiku-5-5',
                   'features': ['messages'], 'retention_terms': 'standard_api',
                   'terms_reference': 'AgreementRecordA1'},
               'erasure': {'outcome': 'known_resources_absent', 'physical_erasure_complete': False,
                   'manual_pending': ['backups_replicas_and_operator_exports'],
                   'raw_body': '9876543210'}}
    normalized = validate_governance(payload)
    assert 'raw_body' not in normalized['erasure']
    store = EvidenceStore(tmp_path / 'private')
    store.write('governance', {'source_commit': COMMIT, **payload})
    assert '9876543210' not in json.dumps(store.read('governance'))
    payload['erasure']['outcome'] = 'preview_only'
    with pytest.raises(ValueError):
        validate_governance(payload)


def test_readiness_keeps_previous_signed_rating_private_and_pending_for_new_run(tmp_path):
    from roma.services.live_acceptance_evidence import update_readiness
    repo = tmp_path / 'repo'
    levels = repo / 'docs/levels'
    levels.mkdir(parents=True)
    for name in ('level-06.md', 'level-07.md', 'level-08.md'):
        (levels / name).write_text('Historical naturalness: 2.5/5.\n- [ ] Full gate\n')
    store = EvidenceStore(tmp_path / 'private')
    store.write('session', dict(source_commit=COMMIT, run_id='r1', status='completed'))
    store.write('physical', dict(source_commit=COMMIT, run_id='r1', events=[]))
    rating_path = store.write('voice_rating', dict(source_commit=COMMIT, run_id='r1',
        listener='Owner', language='gu', naturalness=4))
    original_rating = rating_path.read_bytes()
    update_readiness(repo, store)

    for source_commit, run_id in ((COMMIT, 'r2'), ('b' * 40, 'r1')):
        store.write('session', dict(source_commit=source_commit, run_id=run_id,
                                    status='completed'))
        store.write('physical', dict(source_commit=source_commit, run_id=run_id, events=[]))
        update_readiness(repo, store)

        text = (levels / 'level-06.md').read_text()
        assert f'Run `{run_id}`' in text and source_commit in text
        assert 'No new human listening rating recorded.' in text
        assert '4/5' not in text and '2.5/5' in text
        assert '- [ ] Full gate' in text
        assert rating_path.read_bytes() == original_rating


def test_readiness_rejects_tampered_current_and_previous_ratings(tmp_path):
    import pytest
    from roma.services.live_acceptance_evidence import update_readiness
    for run_id in ('r1', 'r2'):
        store = EvidenceStore(tmp_path / run_id)
        store.write('session', dict(source_commit=COMMIT, run_id=run_id, status='completed'))
        store.write('physical', dict(source_commit=COMMIT, run_id=run_id, events=[]))
        path = store.write('voice_rating', dict(source_commit=COMMIT, run_id='r1',
            listener='Owner', language='gu', naturalness=4))
        value = json.loads(path.read_text())
        value['payload']['naturalness'] = 5
        path.write_text(json.dumps(value))

        with pytest.raises(ValueError, match='Evidence signature or identity mismatch'):
            update_readiness(tmp_path / 'repo', store)


def test_readiness_requires_verified_matching_physical_receipt_before_writing_docs(tmp_path):
    import pytest
    from roma.services.live_acceptance_evidence import update_readiness
    for defect in ('missing', 'tampered', 'source', 'run', 'missing_run'):
        repo = tmp_path / defect / 'repo'
        levels = repo / 'docs/levels'
        levels.mkdir(parents=True)
        original = 'Historical naturalness: 2.5/5.\n- [ ] Full gate\n'
        for name in ('level-06.md', 'level-07.md', 'level-08.md'):
            (levels / name).write_text(original)
        store = EvidenceStore(tmp_path / defect / 'private')
        store.write('session', dict(source_commit=COMMIT,
            run_id=None if defect == 'missing_run' else 'r1', status='completed'))
        if defect != 'missing':
            path = store.write('physical', dict(
                source_commit='b' * 40 if defect == 'source' else COMMIT,
                run_id=None if defect == 'missing_run' else 'r2' if defect == 'run' else 'r1',
                events=[]))
            if defect == 'tampered':
                value = json.loads(path.read_text())
                value['payload']['events'] = [{'event': 'capture_started'}]
                path.write_text(json.dumps(value))

        with pytest.raises((ValueError, OSError)):
            update_readiness(repo, store)

        assert all(path.read_text() == original for path in levels.iterdir())


def test_naturalness_range_keeps_actual_endpoints_in_receipt_and_readiness(tmp_path):
    from roma.services.live_acceptance_evidence import update_readiness
    repo = tmp_path / 'repo'
    levels = repo / 'docs/levels'
    levels.mkdir(parents=True)
    for name in ('level-06.md', 'level-07.md', 'level-08.md'):
        (levels / name).write_text('Historical naturalness: 2.5/5.\n- [ ] Full gate\n')
    store = EvidenceStore(tmp_path / 'private')
    store.write('session', dict(source_commit=COMMIT, run_id='r1', status='capture_failed'))
    store.write('physical', dict(source_commit=COMMIT, run_id='r1', events=[]))
    store.write('voice_rating', dict(source_commit=COMMIT, run_id='r1', listener='Owner',
        language='hi-Hinglish', naturalness=None, naturalness_range=[3, 4]))

    payload = store.read('voice_rating')['payload']
    assert payload['naturalness'] is None
    assert payload['naturalness_range'] == [3, 4]
    assert 'correctness' not in payload
    update_readiness(repo, store)
    update_readiness(repo, store)
    text = (levels / 'level-06.md').read_text()
    assert 'Owner listening naturalness: 3–4/5.' in text
    assert '3.5/5' not in text
    assert 'Historical naturalness: 2.5/5.' in text
    assert '- [ ] Full gate' in text
    assert 'Full multilingual/native acceptance stays open.' in text
    assert text.count('BEGIN CURRENT LIVE ACCEPTANCE') == 1


def test_naturalness_range_requires_one_valid_finite_ordered_rating(tmp_path):
    import pytest
    store = EvidenceStore(tmp_path / 'private')
    base = dict(source_commit=COMMIT, run_id='r1', listener='Owner', language='hi-Hinglish')
    invalid = [None, True, '3-4', {'low': 3, 'high': 4}, [], [3], [3, 4, 5],
               [True, 4], [3, False], [0, 4], [3, 6], [4, 3], [float('nan'), 4],
               [3, float('nan')], [float('-inf'), 4], [3, float('inf')], ['3', 4]]
    for interval in invalid:
        with pytest.raises(ValueError):
            store.write('voice_rating', {**base, 'naturalness': None,
                                         'naturalness_range': interval})
        assert not (store.root / 'voice_rating.json').exists()
    for fields in ({}, {'naturalness': 3, 'naturalness_range': [3, 4]},
                   {'naturalness': True, 'naturalness_range': [3, 4]}):
        with pytest.raises(ValueError):
            store.write('voice_rating', {**base, **fields})
        assert not (store.root / 'voice_rating.json').exists()

    store.write('voice_rating', {**base, 'naturalness_range': [3.25, 4.5]})
    assert store.read('voice_rating')['payload']['naturalness_range'] == [3.25, 4.5]
    store.write('voice_rating', {**base, 'naturalness_range': [3, 3]})
    assert store.read('voice_rating')['payload']['naturalness_range'] == [3, 3]


def test_cli_exact_rating_remains_a_scalar_without_inferred_correctness(tmp_path):
    from scripts.acceptance_live import collect_rating
    store = EvidenceStore(tmp_path / 'private')
    store.write('session', dict(source_commit=COMMIT, run_id='r1'))
    answers = iter(('4', 'skip', 'Voice was clear', 'no', 'unsure'))
    collect_rating(store, listener='Owner', language='hi-Hinglish',
                   ask=lambda prompt: next(answers))
    payload = store.read('voice_rating')['payload']
    assert payload['naturalness'] == 4
    assert 'naturalness_range' not in payload
    assert 'correctness' not in payload
    assert payload['backchannel_preserved_reported'] is False
    assert payload['interruption_worked_reported'] is None
