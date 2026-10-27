"""Engineering test receipts remain incomplete on skips or missing selected outcomes."""

import pytest
from roma.eval.regressions import load_regression_plan, parse_results, summarize_regressions


def test_missing_or_skipped_drill_cannot_be_claimed_as_complete(tmp_path):
    selector = 'tests/integration/test_degraded_acceptance.py::test_retrieval_timeout_cancels_search_before_llm_and_next_call_recovers'
    plan = {'cases': {'drill': [selector]}}
    assert summarize_regressions(plan, [selector], [])[0]['status'] == 'incomplete'
    junit = tmp_path / 'results.xml'
    junit.write_text('<testsuites><testsuite><testcase classname="tests.integration.test_degraded_acceptance" '
                     'name="test_retrieval_timeout_cancels_search_before_llm_and_next_call_recovers" time="0">'
                     '<skipped message="no service"/></testcase></testsuite></testsuites>')
    results = parse_results(junit)
    assert results[0]['outcome'] == 'skipped'
    assert summarize_regressions(plan, [selector], results)[0]['status'] == 'incomplete'


def test_parameterized_failed_drill_and_unselected_native_are_not_green():
    selector = 'tests/services/test_streaming_voice.py::test_minimum_speech_barge_in_cancels_every_stage_and_resumes'
    native = 'tests/repositories/postgres/test_booking_module.py::test_100_http_requests_have_exactly_one_commit'
    plan = {'cases': {'drill': [selector], 'booking': [native]}}
    outcomes = [{'node_id': selector + '[generation]', 'outcome': 'passed'},
                {'node_id': selector + '[synthesis]', 'outcome': 'failure'}]
    cases = summarize_regressions(plan, [selector], outcomes)
    assert cases[0]['status'] == 'failed'
    assert cases[1]['status'] == 'incomplete' and cases[1]['unmeasured'] == [native]


def test_regression_plan_rejects_broad_selectors(tmp_path):
    import json
    plan = tmp_path / 'plan.json'
    plan.write_text(json.dumps({'schema_version': 1, 'cases': {'broad': ['tests']}}))
    with pytest.raises(ValueError, match='explicit reviewed'):
        load_regression_plan(plan)
