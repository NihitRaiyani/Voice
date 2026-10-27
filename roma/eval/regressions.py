"""Run reviewed failure drills, retaining outcomes rather than claiming a native gate."""
from __future__ import annotations

import hashlib
import os
import platform
import re
import signal
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

from roma.eval.benchmark_evidence import read_report
from roma.eval.project_readiness import fingerprint

ROOT = Path(__file__).resolve().parents[2]
PLAN_PATH = ROOT / 'configs/evaluation/chaos-regressions.json'
NODE = re.compile(r'tests/[a-zA-Z0-9_/]+\.py::test_[a-zA-Z0-9_]+\Z')


def load_regression_plan(path=PLAN_PATH):
    plan, _ = read_report(path)
    if plan.get('schema_version') != 1 or not isinstance(plan.get('cases'), dict) or not plan['cases']:
        raise ValueError('A versioned, nonempty regression plan is required')
    for name, nodes in plan['cases'].items():
        if not isinstance(name, str) or not re.fullmatch('[a-z_0-9]+', name):
            raise ValueError('Invalid regression category')
        if not isinstance(nodes, list) or not nodes or len(nodes) > 100:
            raise ValueError('Regression category requires bounded test selectors')
        for node in nodes:
            if not isinstance(node, str) or not NODE.fullmatch(node):
                raise ValueError('Only explicit reviewed pytest test selectors are supported')
            if not (ROOT / node.split('::')[0]).is_file():
                raise ValueError('Regression test source is missing')
    return plan


def select_regressions(*, allow_native=False, plan=None):
    plan = plan or load_regression_plan()
    nodes = list(dict.fromkeys(node for group in plan['cases'].values() for node in group))
    selected = [node for node in nodes if allow_native or '/postgres/' not in node]
    return selected, [node for node in nodes if node not in selected]


def parse_results(path):
    """Keep metadata only: no traceback, provider response, transcript or environment."""
    if not path.is_file():
        return []
    results = []
    for case in ET.parse(path).iter('testcase'):
        name = case.attrib.get('name', '')
        source = case.attrib.get('classname', '').replace('.', '/') + '.py'
        outcome = next((key for key in ('error', 'failure', 'skipped') if case.find(key) is not None), 'passed')
        results.append({'node_id': source + '::' + name, 'outcome': outcome,
                        'duration_secs': float(case.attrib.get('time', '0'))})
    return results


def _belongs(node, selector):
    return node == selector or node.startswith(selector + '[')


def summarize_regressions(plan, selected, results):
    cases = []
    for name, selectors in plan['cases'].items():
        measured = [node for node in selectors if node in selected]
        outcomes = [row for row in results if any(_belongs(row['node_id'], node) for node in measured)]
        missing = [node for node in measured if not any(_belongs(row['node_id'], node) for row in outcomes)]
        counts = Counter(row['outcome'] for row in outcomes)
        complete = len(measured) == len(selectors) and not missing and bool(outcomes) and not counts['skipped']
        cases.append({'case_id': name, 'status': 'passed' if complete and counts['passed'] == len(outcomes)
                      else 'failed' if counts['error'] or counts['failure'] else 'incomplete',
                      'selected': measured, 'unmeasured': [node for node in selectors if node not in selected],
                      'missing_results': missing, 'counts': dict(counts), 'results': outcomes})
    return cases


def _execute_tests(command, timeout_secs, environment):
    # Interrupt pytest first so its disposable-service fixtures can run teardown.
    # Hard termination is a last resort for an unresponsive test subprocess.
    with subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, start_new_session=True, env=environment) as process:
        try:
            output, _ = process.communicate(timeout=timeout_secs)
            return process.returncode, output, False
        except subprocess.TimeoutExpired:
            process.send_signal(signal.SIGINT)
            try:
                output, _ = process.communicate(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                output, _ = process.communicate()
            return process.returncode, output, True


def run_regressions(*, allow_native=False, timeout_secs=300):
    if not 1 <= timeout_secs <= 600:
        raise ValueError('Regression execution timeout must be 1..600 seconds')
    plan = load_regression_plan()
    selected, unmeasured = select_regressions(allow_native=allow_native, plan=plan)
    started = perf_counter()
    with TemporaryDirectory(prefix='roma-regression-') as directory:
        junit = Path(directory) / 'results.xml'
        command = [sys.executable, '-m', 'pytest', '-q', '--tb=short', '-o', 'junit_family=xunit2',
                   f'--junitxml={junit}', *selected]
        # Existing shared SQL fixtures try Docker before their native fallback.
        # A fresh nonexistent socket guarantees no image pulls or working daemon.
        environment = os.environ.copy()
        environment['DOCKER_HOST'] = 'unix://' + str(Path(directory) / 'docker-disabled.sock')
        environment.pop('DOCKER_TLS_VERIFY', None)
        environment.pop('DOCKER_CERT_PATH', None)
        exit_code, output, timed_out = _execute_tests(command, timeout_secs, environment)
        results = parse_results(junit)
        junit_sha256 = hashlib.sha256(junit.read_bytes()).hexdigest() if junit.is_file() else None
    cases = summarize_regressions(plan, selected, results)
    sources = sorted({ROOT / node.split('::')[0] for node in selected})
    # A tree digest covers implementation and shared test fixtures used by the selected suites.
    tree = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for folder in ('roma', 'tests', 'scripts') for path in sorted((ROOT / folder).rglob('*.py'))}
    for path in (ROOT / 'uv.lock', ROOT / 'pyproject.toml', PLAN_PATH):
        tree[str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
    revision = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True,
                              text=True, check=True).stdout.strip()
    successful = not timed_out and exit_code == 0 and bool(results) and all(row['outcome'] == 'passed' for row in results)
    return {'schema_version': 1, 'generated_at': datetime.now(UTC).isoformat(),
            'kind': 'offline_failure_drills_with_disposable_backend' if allow_native else 'offline_failure_drills',
            'revision': revision, 'source_state': 'working_tree', 'source_tree_sha256': fingerprint(tree),
            'selected_test_source_sha256': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                                            for p in sources},
            'configuration_sha256': fingerprint(plan), 'hardware': f'{platform.system()} {platform.machine()}',
            'python_version': platform.python_version(), 'selected_test_selectors': selected,
            'unmeasured_selectors': unmeasured, 'cases': cases, 'counts': dict(Counter(r['outcome'] for r in results)),
            'exit_code': exit_code, 'timed_out': timed_out, 'elapsed_secs': perf_counter() - started,
            'executed_suites_passed': successful,
            'plan_complete': successful and not unmeasured and all(c['status'] == 'passed' for c in cases),
            'junit_sha256': junit_sha256, 'process_output_sha256': hashlib.sha256(output).hexdigest(),
            'native_quality': None, 'native_stop_latency_ms': None, 'production_slo_passed': False,
            'limitations': ['Synthetic/fake AI and transport only; no paid API, model inference or microphone.',
                            'Native SQL/Redis fixtures own temporary loopback services only when explicitly selected; Docker is disabled.',
                            'Test wall times measure regression execution, never acoustic latency or production SLOs.',
                            'Passed engineering drills require separate review and do not grant product approval.']}


def regression_receipts(report):
    """Observed pytest outcomes become unapproved engineering receipts only."""
    by_case = {row['case_id']: row for row in report['cases']}
    groups = {'chaos.redis_loss': ['redis_sql_recovery'],
              'chaos.provider_timeout': ['slow_stt', 'slow_llm', 'slow_tts'],
              'chaos.queue_pressure': ['queue_pressure'], 'chaos.disconnect': ['disconnect'],
              'chaos.rag_timeout': ['rag_timeout'],
              'chaos.rate_limit_budget': ['provider_429_5xx_budget']}
    identity = {'revision': report['revision'], 'dataset_sha256': report['source_tree_sha256'],
                'configuration_sha256': report['configuration_sha256'], 'hardware': report['hardware'],
                'conditions': 'offline fault injection; fake AI; disposable backend only where selected'}
    receipts = []
    for check_id, names in groups.items():
        cases = [by_case[name] for name in names]
        if any(case['status'] == 'incomplete' for case in cases):
            continue
        passed = sum(case['counts'].get('passed', 0) for case in cases)
        failed = sum(case['counts'].get('failure', 0) + case['counts'].get('error', 0) for case in cases)
        receipts.append({'check_id': check_id, 'kind': 'synthetic', 'samples': passed + failed,
                         'metrics': {'passed_scenarios': passed, 'failed_scenarios': failed},
                         'identity': identity, 'source': 'reviewed pytest selectors; hashed JUnit outcome evidence',
                         'approved': False})
    race = by_case.get('booking_100_race')
    if race and race['status'] == 'passed':
        # The reviewed test asserts 100 HTTP results and queries SQL for one row.
        receipts.append({'check_id': 'booking.race100', 'kind': 'disposable_backend', 'samples': 100,
                         'metrics': {'winners': 1, 'conflicts': 99, 'attempts': 100},
                         'identity': identity, 'source': 'fresh passing assertions: test_100_http_requests_have_exactly_one_commit',
                         'approved': False})
    return receipts
