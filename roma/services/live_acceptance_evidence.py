"""Owner-only local receipts: HMAC proves integrity, not human or acoustic truth."""
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

KINDS = frozenset({'session', 'physical', 'voice_rating', 'governance'})


def _canonical(data):
    return json.dumps(data, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def private_file(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1
                or info.st_size > 65536):
            raise ValueError('Owner-only bounded regular file required')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            return stream.read(65537)
    finally:
        os.close(fd)


def atomic_private(path, raw):
    if len(raw) > 65536:
        raise ValueError('Evidence too large')
    if path.exists() or path.is_symlink():
        private_file(path)
    temporary = path.with_name('.' + uuid4().hex)
    fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


class EvidenceStore:
    """read returns the verified envelope; the signing key never enters receipts."""
    def __init__(self, root: Path, *, create=True):
        self.root = Path(root).absolute()
        if any(p.is_symlink() for p in (self.root, *self.root.parents)):
            raise ValueError('Symlink evidence paths are forbidden')
        if create:
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.root.stat()
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError('Owner-only evidence directory required')
        path = self.root / '.key'
        if create:
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            except FileExistsError:
                pass
            else:
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(secrets.token_bytes(32))
                    stream.flush()
                    os.fsync(stream.fileno())
        self._key = private_file(path)
        if len(self._key) != 32:
            raise ValueError('Invalid evidence signing key')

    def _path(self, kind):
        if kind not in KINDS:
            raise ValueError('Unknown evidence kind')
        return self.root / f'{kind}.json'

    def write(self, kind: str, payload: dict) -> Path:
        path = self._path(kind)
        payload = payload.copy()
        if kind == 'voice_rating':
            if not {'source_commit', 'run_id', 'listener', 'language'} <= payload.keys():
                raise ValueError('Actual listener and language required')
            _naturalness_label(payload)
            if payload.get('correctness') is not None:
                _score(payload['correctness'])
            for field in ('listener', 'language'):
                _text(payload[field])
            if payload.get('notes'):
                _text(payload['notes'])
        if kind == 'governance':
            common = {k: v for k, v in payload.items() if k in {'source_commit', 'run_id'}}
            payload = {**common, **validate_governance(
                {k: v for k, v in payload.items() if k not in common})}
        commit = payload.pop('source_commit', '')
        if not re.fullmatch(r'[0-9a-f]{40}', commit):
            raise ValueError('Exact source commit required')
        envelope = dict(schema_version=1, kind=kind, source_commit=commit,
                        recorded_at=datetime.now(UTC).isoformat().replace('+00:00', 'Z'),
                        key_fingerprint=hashlib.sha256(self._key).hexdigest(), payload=payload)
        envelope['hmac_sha256'] = hmac.new(self._key, _canonical(envelope), 'sha256').hexdigest()
        atomic_private(path, _canonical(envelope))
        return path

    def read(self, kind: str) -> dict:
        envelope = json.loads(private_file(self._path(kind)))
        signature = envelope.pop('hmac_sha256', '')
        if (not isinstance(signature, str) or not hmac.compare_digest(
                signature, hmac.new(self._key, _canonical(envelope), 'sha256').hexdigest())
                or envelope.get('kind') != kind or envelope.get('schema_version') != 1
                or envelope.get('key_fingerprint') != hashlib.sha256(self._key).hexdigest()
                or not re.fullmatch(r'[0-9a-f]{40}', envelope.get('source_commit', ''))):
            raise ValueError('Evidence signature or identity mismatch')
        return {**envelope, 'hmac_sha256': signature}


def _text(value):
    if (not isinstance(value, str) or not value.strip() or len(value) > 500
            or any(ord(c) < 32 for c in value)
            or re.search(r'(?i)\b(tbd|placeholder|recommended|unknown|example|pending|todo)\b', value)
            or re.search(r'\b(?:\+91[- ]?)?[6-9]\d{9}\b|\S+@\S+|sk-ant-|(?i:bearer )', value)):
        raise ValueError('Actual bounded non-secret metadata required')
    return value.strip()


def _score(value):
    if type(value) not in (int, float) or not 1 <= value <= 5:
        raise ValueError('An actual finite 1–5 listening score is required')
    return value


def _naturalness_label(payload):
    score = payload.get('naturalness')
    if score is not None:
        if 'naturalness_range' in payload:
            raise ValueError('Use one naturalness score or range')
        return f'{_score(score):g}'
    interval = payload.get('naturalness_range')
    if not isinstance(interval, (list, tuple)) or len(interval) != 2:
        raise ValueError('An actual two-endpoint naturalness range is required')
    low, high = (_score(value) for value in interval)
    if low > high:
        raise ValueError('Naturalness range endpoints must be ordered')
    return f'{low:g}–{high:g}'


def validate_governance(payload):
    required = {'approver', 'approved_at', 'policy_reference', 'audio_retention_days',
                'transcript_retention_days', 'anthropic', 'erasure'}
    if set(payload) != required:
        raise ValueError('Exact confirmed governance fields required')
    for field in ('approver', 'policy_reference'):
        _text(payload[field])
    approved = datetime.fromisoformat(_text(payload['approved_at']).replace('Z', '+00:00'))
    if approved.tzinfo is None or approved > datetime.now(UTC):
        raise ValueError('Actual past approval timestamp required')
    for field in ('audio_retention_days', 'transcript_retention_days'):
        if type(payload[field]) is not int or not 0 <= payload[field] <= 3650:
            raise ValueError('Explicit bounded retention days required')
    anthropic = payload['anthropic']
    if not isinstance(anthropic, dict) or set(anthropic) != {
            'organization_reference', 'model', 'features', 'retention_terms', 'terms_reference'}:
        raise ValueError('Actual account/model/feature agreement evidence required')
    if anthropic['model'] != 'claude-haiku-5-5':
        raise ValueError('Configured model evidence differs')
    for field in ('organization_reference', 'retention_terms', 'terms_reference'):
        _text(anthropic[field])
    if (not isinstance(anthropic['features'], list) or not anthropic['features']
            or len(anthropic['features']) > 10):
        raise ValueError('Confirmed active features required')
    for feature in anthropic['features']:
        _text(feature)
    erasure = payload['erasure']
    if (not isinstance(erasure, dict) or erasure.get('outcome') != 'known_resources_absent'
            or erasure.get('physical_erasure_complete') is not False
            or not isinstance(erasure.get('manual_pending'), list)
            or not erasure['manual_pending']):
        raise ValueError('Preview is not erasure; known-resource receipts retain manual scope')
    if len(erasure['manual_pending']) > 20:
        raise ValueError('Erasure scope exceeds its bound')
    for item in erasure['manual_pending']:
        _text(item)
    normalized = json.loads(_canonical(payload))
    normalized['erasure'] = {key: erasure[key] for key in (
        'outcome', 'physical_erasure_complete', 'manual_pending')}
    return normalized


def update_readiness(repo_root, store):
    """Only signed local observation blocks change; full roadmap gates never do."""
    session = store.read('session')
    current = session['payload']
    physical = store.read('physical')
    if (not isinstance(current.get('run_id'), str) or not current['run_id']
            or physical['payload'].get('run_id') != current['run_id']
            or physical['source_commit'] != session['source_commit']):
        raise ValueError('Physical and session identities differ')
    rating = None
    if (store.root / 'voice_rating.json').exists():
        # Verify even a previous run's receipt before leaving its score private.
        value = store.read('voice_rating')
        if (value['payload'].get('run_id') == current.get('run_id')
                and value['source_commit'] == session['source_commit']):
            rating = value['payload']
    common = (f"Recorded {session['recorded_at']}; source commit `{session['source_commit']}`. "
              f"Run `{current.get('run_id', 'unidentified')}`; status `{current.get('status', 'unknown')}`. "
              'Local HMAC receipts establish integrity, not independent acceptance. ')
    descriptions = {
        'level-06.md': (f"Owner listening naturalness: {_naturalness_label(rating)}/5. "
                        if rating else 'No new human listening rating recorded. ') +
                       'Historical scores remain below. Full multilingual/native acceptance stays open.',
        'level-07.md': 'Acoustic first-audible and playback-stop latency remain unmeasured without independent instrumentation. Software timing and one supervised scenario do not pass the full reliability/concurrency gate.',
        'level-08.md': 'Governance, provider-account terms and whole-person/backup erasure remain pending independent closure. No recording or retention policy is activated by this receipt.'}
    if (store.root / 'governance.json').exists():
        governance = store.read('governance')
        descriptions['level-08.md'] = ('Operator-supplied governance metadata recorded at ' +
            governance['recorded_at'] + '; references are retained privately. Known-resource erasure retains manual scope; no legal-compliance/full privacy gate is inferred.')
    begin, end = '<!-- BEGIN CURRENT LIVE ACCEPTANCE -->', '<!-- END CURRENT LIVE ACCEPTANCE -->'
    updates = {}
    for name, description in descriptions.items():
        path = Path(repo_root) / 'docs/levels' / name
        if path.is_symlink():
            raise ValueError('Readiness source cannot be a symlink')
        text = path.read_text()
        if text.count(begin) != text.count(end) or text.count(begin) > 1:
            raise ValueError('Readiness block is ambiguous')
        block = f'{begin}\n\n## Current live acceptance\n\n{common}{description}\n\n{end}'
        if begin in text:
            text = text[:text.index(begin)] + block + text[text.index(end) + len(end):]
        else:
            text = text.rstrip() + '\n\n' + block + '\n'
        updates[path] = text
    for path, text in updates.items():
        path.write_text(text)
    return updates
