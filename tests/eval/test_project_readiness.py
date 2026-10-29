"""Public evaluation receipts cannot promote synthetic evidence to native gates."""
from roma.eval.project_readiness import build_readiness, load_matrix


def test_missing_mandatory_evidence_stays_unknown_and_gate_closed():
    report = build_readiness(load_matrix(), [])
    assert report['full_gate_passed'] is False
    assert len(report['checks']) >= 30
    asr = next(row for row in report['checks'] if row['id'] == 'asr.gu')
    assert asr['metrics']['wer'] is None and asr['status'] == 'pending'


def receipt(kind='mock'):
    return {'check_id':'llm.scripted_turns','kind':kind,'samples':70,
            'metrics': {'json_validity_rate':1,'ttft_p95_ms':0.5,'input_tokens':None,'output_tokens':None},
            'identity': {'revision':'b638f7d','dataset_sha256':'a'*64,'configuration_sha256':'b'*64,
                         'hardware':'synthetic','conditions':'warm mock'},
            'source':'public-fixture-v1','approved':False}


def test_mock_measurements_remain_separate_from_unmeasured_native_metrics():
    report = build_readiness(load_matrix(), [receipt()])
    row = next(r for r in report['checks'] if r['id']=='llm.scripted_turns')
    assert row['metrics']['ttft_p95_ms'] is None
    assert row['evidence'][0]['metrics']['ttft_p95_ms']==0.5
    assert report['full_gate_passed'] is False


def test_missing_token_usage_and_unset_targets_cannot_pass_native_review():
    evidence = receipt('native')
    evidence['approved']=True
    row = next(r for r in build_readiness(load_matrix(),[evidence])['checks'] if r['id']==evidence['check_id'])
    assert row['status']=='pending'


def test_nonfinite_measurement_rejected_instead_of_reported_as_success():
    import pytest
    evidence=receipt()
    evidence['metrics']['ttft_p95_ms']=float('nan')
    with pytest.raises(ValueError,match='finite'):
        build_readiness(load_matrix(),[evidence])


def test_chaos_plan_never_silently_runs_or_claims_native_services_without_optin():
    from roma.eval.regressions import select_regressions
    selected,unmeasured=select_regressions(allow_native=False)
    assert selected and all('/postgres/' not in n for n in selected)
    assert any('/postgres/' in n for n in unmeasured)


def test_booking_acceptance_requires_exact_one_winner_not_zero_winners():
    evidence = receipt('disposable_backend')
    evidence.update(check_id='booking.race100', samples=100, approved=True,
                    metrics={'winners': 0, 'conflicts': 100, 'attempts': 100})
    row = next(r for r in build_readiness(load_matrix(), [evidence])['checks']
               if r['id'] == 'booking.race100')
    assert row['status'] == 'pending'
    evidence['metrics'].update(winners=1, conflicts=99)
    row = next(r for r in build_readiness(load_matrix(), [evidence])['checks']
               if r['id'] == 'booking.race100')
    assert row['status'] == 'passed'


def test_rag_quality_accepts_only_reviewed_native_agreed_top_five_measurements():
    evidence = receipt('native')
    evidence.update(check_id='rag.quality', samples=38, approved=True, metrics={
        'recall_at_5': 0.80, 'mrr': 0.70, 'groundedness': 0.90,
        'deferral_accuracy': 0.95, 'safety_rate': 0.95,
    })
    matrix = load_matrix()

    def status():
        return next(row['status'] for row in build_readiness(matrix, [evidence])['checks']
                    if row['id'] == 'rag.quality')

    assert status() == 'passed'
    for metric, value in evidence['metrics'].copy().items():
        evidence['metrics'][metric] = value - 0.01
        assert status() == 'pending'
        evidence['metrics'][metric] = value
    evidence['metrics']['groundedness'] = None
    assert status() == 'pending'
    evidence['metrics']['groundedness'] = 1
    evidence['kind'] = 'synthetic'
    assert status() == 'pending'
    evidence['kind'] = 'native'
    evidence['approved'] = False
    assert status() == 'pending'


def test_rag_retrieval_target_does_not_invent_an_answer_latency_target():
    evidence = receipt('native')
    evidence.update(check_id='rag.latency', samples=38, approved=True,
                    metrics={'retrieval_p95_ms': 450, 'answer_p95_ms': 0})
    row = next(row for row in build_readiness(load_matrix(), [evidence])['checks']
               if row['id'] == 'rag.latency')
    assert row['targets']['retrieval_p95_ms'] == {'direction': 'max', 'value': 450}
    assert row['targets']['answer_p95_ms'] is None
    assert row['status'] == 'pending'


def test_new_failed_measurement_cannot_borrow_a_historical_passing_gate():
    from copy import deepcopy

    approved = receipt('native')
    approved.update(check_id='rag.quality', samples=38, approved=True, metrics={
        'recall_at_5': .90, 'mrr': .80, 'groundedness': .95,
        'deferral_accuracy': 1.0, 'safety_rate': 1.0,
    })
    changed = deepcopy(approved)
    changed['identity']['configuration_sha256'] = 'c' * 64
    changed['metrics']['groundedness'] = .10
    row = next(r for r in build_readiness(load_matrix(), [approved, changed])['checks']
               if r['id'] == 'rag.quality')
    assert row['metrics']['groundedness'] == .10 and row['status'] == 'pending'
    assert len(row['evidence']) == 2


def test_new_unapproved_configuration_does_not_inherit_old_approval():
    from copy import deepcopy

    approved = receipt('native')
    approved.update(check_id='rag.quality', samples=38, approved=True, metrics={
        'recall_at_5': .90, 'mrr': .80, 'groundedness': .95,
        'deferral_accuracy': 1.0, 'safety_rate': 1.0,
    })
    changed = deepcopy(approved)
    changed['identity']['configuration_sha256'] = 'c' * 64
    changed['approved'] = False
    row = next(r for r in build_readiness(load_matrix(), [approved, changed])['checks']
               if r['id'] == 'rag.quality')
    assert row['status'] == 'pending' and len(row['evidence']) == 2
