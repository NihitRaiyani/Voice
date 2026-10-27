"""Metadata-only acceptance matrix: completed labs cannot grant product approval."""
from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime
from pathlib import Path

from roma.eval.benchmark_evidence import read_report

ROOT = Path(__file__).resolve().parents[2]
MATRIX_PATH = ROOT / 'configs/evaluation/mandatory-matrix.json'
KINDS = {'mock', 'synthetic', 'native', 'human', 'real_call', 'account_review', 'disposable_backend'}
IDENTITY = ('revision', 'dataset_sha256', 'configuration_sha256', 'hardware', 'conditions')


def load_matrix(path: Path = MATRIX_PATH) -> dict:
    matrix, _ = read_report(path)
    rows = matrix['checks']
    if not rows or len({r['id'] for r in rows}) != len(rows):
        raise ValueError('Matrix requires unique checks')
    return matrix


def _receipt(receipt: dict, specs: dict) -> dict:
    if set(receipt) != {'check_id', 'kind', 'samples', 'metrics', 'identity', 'source', 'approved'}:
        raise ValueError('Receipt must contain only reviewed metadata fields')
    if receipt['check_id'] not in specs or receipt['kind'] not in KINDS:
        raise ValueError('Unknown check or evidence kind')
    spec = specs[receipt['check_id']]
    if type(receipt['samples']) is not int or receipt['samples'] < 1:
        raise ValueError('Receipt requires a positive sample count')
    if type(receipt['approved']) is not bool:
        raise ValueError('Approval must be explicit')
    metrics = receipt['metrics']
    if set(metrics) != set(spec['metrics']):
        raise ValueError('Receipt metrics must match its check')
    for value in metrics.values():
        if value is not None and (type(value) not in (float, int) or not math.isfinite(value) or value < 0):
            raise ValueError('Measurements must be finite nonnegative numbers or null')
    identity = receipt['identity']
    if set(identity) != set(IDENTITY):
        raise ValueError('Receipt requires reproducible identity')
    for value in (*identity.values(), receipt['source']):
        if not isinstance(value, str) or not value or len(value) > 180 or '\n' in value:
            raise ValueError('Evidence identity labels must be bounded single lines')
    for key in ('dataset_sha256', 'configuration_sha256'):
        value = identity[key]
        if len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
            raise ValueError('Dataset/configuration identities must be SHA256')
    return receipt


def _meets_target(value, target):
    if target is None:
        return False
    if target['direction'] == 'exact':
        return value == target['value']
    if target['direction'] == 'max':
        return value <= target['value']
    if target['direction'] == 'min':
        return value >= target['value']
    raise ValueError('Unknown acceptance target direction')


def build_readiness(matrix: dict, receipts: list[dict]) -> dict:
    specs = {r['id']: r for r in matrix['checks']}
    by_check: dict[str, list[dict]] = {key: [] for key in specs}
    for receipt in receipts:
        validated = _receipt(receipt, specs)
        by_check[validated['check_id']].append(validated)
    checks = []
    for key, spec in specs.items():
        evidence = by_check[key]
        eligible = [r for r in evidence if r['kind'] in spec['accepted_kinds']]
        approved = [r for r in eligible if r['approved'] and r['samples'] >= spec['minimum_samples']
                    and all(v is not None for v in r['metrics'].values())]
        passed = any(all(
            _meets_target(r['metrics'][metric], target)
            for metric, target in spec['targets'].items()
        ) for r in approved)
        latest = eligible[-1] if eligible else None
        checks.append({**spec, 'status': 'passed' if passed else 'pending',
                       'metrics': latest['metrics'] if latest else dict.fromkeys(spec['metrics']),
                       'evidence': evidence})
    return {'schema_version': 1, 'generated_at': datetime.now(UTC).isoformat(),
            'full_gate_passed': all(r['status'] == 'passed' for r in checks), 'checks': checks,
            'limitations': ['Evidence acceptance requires independent review; CLI never grants approval.',
                            'Mock/backend concurrency is not Claude, speech GPU or real-call capacity.']}


def fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
