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
