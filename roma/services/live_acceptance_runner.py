"""Guided local acceptance helpers. No device/provider construction on import."""
import fcntl
import json
import math
import os
import re
from collections import deque
from datetime import UTC, datetime
from pathlib import Path

from roma.services.live_acceptance_evidence import private_file


def _exception_graph(error):
    """Visit at most sixteen actual exceptions, including suppressed contexts."""
    pending, seen = [(error, None, 'root')], set()
    while pending and len(seen) < 16:
        current, parent, relation = pending.pop()
        if id(current) in seen:
            continue
        index = len(seen)
        seen.add(id(current))
        yield current, parent, relation
        for related, label in ((current.__context__, 'context'), (current.__cause__, 'cause')):
            if related is not None:
                pending.append((related, index, label))
        if isinstance(current, BaseExceptionGroup):
            pending.extend((child, index, 'group') for child in reversed(current.exceptions[:16]))


def failure_diagnostics(error):
    """Bounded code locations only, including contexts hidden by ``from None``."""
    root = Path(__file__).resolve().parents[2]
    result = []
    for current, parent, relation in _exception_graph(error):
        kind = type(current)
        name, module = kind.__name__, kind.__module__
        if (not isinstance(module, str)
                or (module != 'builtins' and not module.startswith(('roma.', 'asyncio.')))
                or not name.isidentifier() or len(name) > 80):
            name = 'ExternalError'
        frames = deque(maxlen=8)
        trace = current.__traceback__
        while trace is not None:
            try:
                path = Path(trace.tb_frame.f_code.co_filename).resolve().relative_to(root)
            except (ValueError, OSError, RuntimeError):
                pass
            else:
                # Only repository Python source paths; never runtime filenames,
                # external absolute paths, source lines or frame locals.
                if (path.parts and path.parts[0] in {'roma', 'scripts', 'tests'} and path.suffix == '.py'
                        and len(path.as_posix()) <= 180
                        and all(re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', part)
                                for part in (*path.parts[:-1], path.stem))):
                    frames.append({'path': path.as_posix(), 'line': trace.tb_lineno})
            trace = trace.tb_next
        result.append({'exception_type': name, 'parent': parent,
                       'relation': relation, 'frames': list(frames)})
    return result


def reserve_budget(path, language):
    """Reserve INR2, never refund an ambiguous launch, preserve the INR50 history."""
    if language not in {'gu', 'hi', 'en'}:
        raise ValueError('Unsupported acceptance language')
    private_file(path)
    fd = os.open(path, os.O_RDWR | os.O_APPEND | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        raw = os.read(fd, 65537)
        if len(raw) > 65536:
            raise ValueError('Budget history exceeds its bound')
        rows = [json.loads(line) for line in raw.splitlines()]
        if any(not isinstance(row, dict) or type(row.get('reserved_paise')) is not int
               or not 0 < row['reserved_paise'] <= 5000 for row in rows):
            raise ValueError('Unknown budget history')
        previous = sum(row['reserved_paise'] for row in rows)
        if previous < 600:
            raise ValueError('Historical acceptance reservations are incomplete')
        total = previous + 200
        if total > 5000:
            raise ValueError('Aggregate INR50 acceptance budget exhausted')
        row = {'at': datetime.now(UTC).isoformat(), 'reserved_paise': 200,
               'invocation': len(rows) + 1, 'language': language}
        os.write(fd, (json.dumps(row) + '\n').encode())
        os.fsync(fd)
        return total
    finally:
        os.close(fd)


class AcceptanceObserver:
    """Metadata-only instrumentation; prepared CLOSE state is a scenario, not discovery."""
    prepared_scenario = True
    full_discovery_verified = False

    def __init__(self, run_id, *, emit=print):
        self.run_id, self.emit = run_id, emit
        self.events, self.turns = [], []
        self.sid = None
        self.expected_database = None
        self.claim_times = {}
        self.played_epochs = set()
        self.capture_health = None

    def require_target(self, url):
        from roma.repositories.postgres.demo_database import validate_demo_target
        validate_demo_target(url, 'test')
        if url != self.expected_database:
            raise ValueError('Acceptance requires its owned TEST ONLY database')

    def prepare_state(self, state):
        from roma.domain.conversation.stage import ConversationStage
        self.sid = state.call_sid
        state.stage = ConversationStage.CLOSE
        state.inquiry_confirmed = True
        state.lead_name, state.current_status = 'Test Student', 'student'
        state.education, state.passing_year, state.city = 'graduate', '2026', 'Vadodara'
        state.booking_required = True

    def on_event(self, kind, payload):
        from time import perf_counter
        allowed = {
            'capture_started': set(), 'device_opened': set(),
            'tts_warmup': {'tts_readiness_ms'},
            'capture_recovered': {'recovery_count'},
            'playback_requested': {'turn_epoch'},
            'interruption_claimed': {'turn_epoch', 'was_playing'},
            'output_cleared': {'turn_epoch', 'had_output', 'software_clear_ms',
                               'software_stop_after_claim_ms'}}
        if (kind not in allowed or not isinstance(payload, dict)
                or not payload.keys() <= allowed[kind] or len(self.events) >= 200):
            raise ValueError('Invalid or excessive acceptance events')
        for field, value in payload.items():
            if field in {'was_playing', 'had_output'}:
                if type(value) is not bool:
                    raise ValueError('Expected a measured boolean')
            elif value is not None and (type(value) not in (int, float)
                    or not math.isfinite(value) or not 0 <= value <= 3600000):
                raise ValueError('Expected a finite nonnegative measurement')
        self.events.append({'event': kind, 'at_monotonic': perf_counter(), **payload})
        self.emit(json.dumps({'acceptance_event': kind, **payload}, sort_keys=True))

    def on_result(self, outcome, state):
        from dataclasses import asdict
        self.turns.append(asdict(outcome))
        if len(self.turns) > 12:
            raise ValueError('Acceptance turn cap exceeded')
        if state.appointment_id:
            # Production service supplied this only after SQL commit. Independent
            # SQL/restore verification still follows before reporting evidence.
            state.lead_wants_out = True
        else:
            self.emit('NEXT: while Roma speaks, say a short haan/okay once. Then interrupt '
                      'to change the Vadodara visit to tomorrow 3pm in your selected language. '
                      'After the corrected readback, explicitly confirm if correct. '
                      'Do not confirm an incorrect time.')

    def voice_type(self, base):
        observer = self
        class ObservedVoice(base):
            def claim_turn(self):
                from time import perf_counter
                busy = self._busy
                playing = getattr(self.speaker, 'playback_active', False) is True
                epoch = super().claim_turn()
                if busy:
                    observer.claim_times[epoch] = perf_counter()
                    observer.on_event('interruption_claimed', {'turn_epoch': epoch, 'was_playing': playing})
                return epoch
        return ObservedVoice

    def device_type(self, base):
        observer = self
        class ObservedDevice(base):
            async def start(self):
                await super().start()
                observer.on_event('device_opened', {})

            async def play(self, result, *, epoch=None):
                owner = self._epoch(result.turn_epoch if epoch is None else epoch)
                if owner not in observer.played_epochs:
                    observer.played_epochs.add(owner)
                    observer.on_event('playback_requested', {'turn_epoch': owner})
                    observer.emit('ROMA RESPONSE: first try one short haan/okay; on the next response '
                                  'interrupt mid-sentence with the changed time. This cue is software scheduling, not acoustic onset.')
                return await super().play(result, epoch=epoch)

            async def aclose(self):
                try:
                    return await super().aclose()
                finally:
                    observer.capture_health = self.capture_health

            async def clear(self):
                from time import perf_counter
                owner, active = self._epoch(), self.output_pending
                started = perf_counter()
                try:
                    return await super().clear()
                finally:
                    ended = perf_counter()
                    claim = observer.claim_times.pop(owner, None)
                    observer.on_event('output_cleared', {
                        'turn_epoch': owner, 'had_output': active,
                        'software_clear_ms': (ended - started) * 1000,
                        'software_stop_after_claim_ms': (ended - claim) * 1000 if claim else None})
        return ObservedDevice


async def run_session(invoke, verify, observer, store, source):
    """Always save a signed outcome; exceptions never turn into passed checks."""
    status, booking = 'failed', {'committed': None, 'restored': None}
    diagnostics = []
    try:
        await invoke()
        booking = await verify()
        status = 'completed'
    except BaseException as exc:
        import asyncio
        diagnostics = failure_diagnostics(exc)
        if any(isinstance(error, (KeyboardInterrupt, asyncio.CancelledError))
               for error, _, _ in _exception_graph(exc)):
            status = 'interrupted'
        raise
    finally:
        common = {**source, 'run_id': observer.run_id}
        store.write('physical', {**common, 'events': observer.events,
                    'turn_budget': observer.turns, 'capture_health': observer.capture_health,
                    'acoustic_stop_ms': None,
                    'acoustic_first_audible_ms': None, 'full_gate_passed': False,
                    'prepared_booking_scenario': True})
        store.write('session', {**common, 'status': status, 'booking': booking,
                    'booking_verified': (status == 'completed' and booking.get('committed') is True
                                         and booking.get('restored') is True),
                    'failure_diagnostics': diagnostics,
                    'microphone_used': any(e['event'] in {'capture_started', 'device_opened'} for e in observer.events), 'recording_retained': False,
                    'full_discovery_verified': False, 'full_gate_passed': False})
    return booking
