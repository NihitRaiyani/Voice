"""Live runner contracts use fakes; no devices, models, paid API or working DB."""
import json
import os

import pytest
from roma.services.live_acceptance_runner import reserve_budget


def test_budget_counts_history_and_never_resets_between_launches(tmp_path):
    path = tmp_path / 'reservations.jsonl'
    path.write_text(''.join(json.dumps({'reserved_paise': 200}) + '\n' for _ in range(3)))
    os.chmod(path, 0o600)
    assert reserve_budget(path, 'gu') == 800
    assert reserve_budget(path, 'gu') == 1000
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert sum(row['reserved_paise'] for row in rows) == 1000


def test_budget_refuses_exhaustion_malformed_and_missing_history(tmp_path):
    path = tmp_path / 'reservations.jsonl'
    with pytest.raises((ValueError, OSError)):
        reserve_budget(path, 'gu')
    path.write_text(json.dumps({'reserved_paise': 4900}) + '\n')
    os.chmod(path, 0o600)
    with pytest.raises(ValueError):
        reserve_budget(path, 'gu')
    path.write_text('{broken\n')
    with pytest.raises(ValueError):
        reserve_budget(path, 'gu')


def test_prepared_demo_does_not_invent_booking_or_completed_discovery_evidence():
    from roma.domain.conversation.state import CallState
    from roma.services.live_acceptance_runner import AcceptanceObserver
    observer = AcceptanceObserver('test-run', emit=lambda value: None)
    state = CallState(call_sid='voice-lab-test')
    observer.prepare_state(state)
    assert state.stage.value == 'close' and state.lead_name == 'Test Student'
    assert state.booking_required and not state.appointment_id and not state.locked_slot
    assert observer.prepared_scenario and not observer.full_discovery_verified


def test_fake_live_session_saves_observations_without_fabricating_acoustic_gate(tmp_path):
    import asyncio

    from roma.services.live_acceptance_evidence import EvidenceStore
    from roma.services.live_acceptance_runner import AcceptanceObserver, run_session
    async def run():
        store = EvidenceStore(tmp_path / 'private')
        observer = AcceptanceObserver('test-run', emit=lambda value: None)
        async def invoke():
            observer.on_event('interruption_claimed', {'turn_epoch': 2})
            observer.on_event('output_cleared', {'software_clear_ms': 12.0})
        async def verify():
            return {'committed': True, 'restored': True}
        await run_session(invoke, verify, observer, store,
                          {'source_commit': 'a' * 40, 'source_tree_sha256': 'b' * 64})
        session = store.read('session')['payload']
        physical = store.read('physical')['payload']
        assert session['booking']['committed'] and session['status'] == 'completed'
        assert physical['acoustic_stop_ms'] is None and not physical['full_gate_passed']
        assert len(physical['events']) == 2
    asyncio.run(run())


def test_failed_live_session_retains_signed_failure_and_does_not_verify_booking(tmp_path):
    import asyncio

    from roma.services.live_acceptance_evidence import EvidenceStore
    from roma.services.live_acceptance_runner import AcceptanceObserver, run_session
    async def run():
        store = EvidenceStore(tmp_path / 'private')
        async def invoke():
            raise ValueError('private provider detail')
        async def verify():
            raise AssertionError('must not verify a failed session')
        with pytest.raises(ValueError):
            await run_session(invoke, verify, AcceptanceObserver('r', emit=lambda value: None),
                              store, {'source_commit': 'a' * 40})
        receipt = store.read('session')['payload']
        assert receipt['status'] == 'failed' and receipt['booking']['committed'] is None
        assert receipt['booking']['restored'] is None and receipt['booking_verified'] is False
        assert 'private provider detail' not in json.dumps(receipt)
    asyncio.run(run())


@pytest.mark.parametrize('wrapper', ['cause', 'suppressed_context', 'group'])
def test_wrapped_cancellation_keeps_interrupted_status_and_original_diagnostics(tmp_path, wrapper):
    import asyncio

    from roma.core.voice_errors import VoiceErrorCode, VoiceFailure
    from roma.services.live_acceptance_evidence import EvidenceStore
    from roma.services.live_acceptance_runner import AcceptanceObserver, run_session

    async def run():
        store = EvidenceStore(tmp_path / 'private')
        private = 'private cancellation details'

        async def invoke():
            if wrapper == 'group':
                raise BaseExceptionGroup(private, [asyncio.CancelledError(private)])
            try:
                raise asyncio.CancelledError(private)
            except asyncio.CancelledError as cancellation:
                if wrapper == 'cause':
                    raise VoiceFailure(VoiceErrorCode.DB) from cancellation
                raise VoiceFailure(VoiceErrorCode.DB) from None

        async def verify():
            pytest.fail('interrupted sessions cannot verify')

        with pytest.raises((VoiceFailure, BaseExceptionGroup)):
            await run_session(invoke, verify, AcceptanceObserver('r', emit=lambda value: None),
                              store, {'source_commit': 'a' * 40})
        receipt = store.read('session')['payload']
        assert receipt['status'] == 'interrupted' and receipt['booking_verified'] is False
        assert 'CancelledError' in {node['exception_type'] for node in receipt['failure_diagnostics']}
        assert private not in json.dumps(receipt)

    asyncio.run(run())


def test_failure_diagnostics_retain_nested_origins_without_private_details(tmp_path):
    import asyncio

    from roma.core.voice_errors import VoiceErrorCode, VoiceFailure, voice_boundary
    from roma.services.live_acceptance_evidence import EvidenceStore
    from roma.services.live_acceptance_runner import AcceptanceObserver, run_session

    async def run():
        store = EvidenceStore(tmp_path / 'private')
        private = 'sk-ant-private-caller-9876543210'

        async def invoke():
            with voice_boundary(VoiceErrorCode.CAPTURE):
                try:
                    raise ValueError(private)
                except ValueError as original:
                    raise ExceptionGroup(private, [original]) from None

        async def verify():
            raise AssertionError('failed sessions cannot verify')

        with pytest.raises(VoiceFailure):
            await run_session(invoke, verify, AcceptanceObserver('r', emit=lambda value: None),
                              store, {'source_commit': 'a' * 40})
        diagnostics = store.read('session')['payload']['failure_diagnostics']
        assert {node['exception_type'] for node in diagnostics} >= {
            'VoiceFailure', 'ExceptionGroup', 'ValueError'}
        assert any(frame['path'] == 'tests/services/test_live_acceptance_runner.py'
                   and type(frame['line']) is int
                   for node in diagnostics for frame in node['frames'])
        serialized = json.dumps(diagnostics)
        assert private not in serialized and str(tmp_path) not in serialized
        assert '/Users/' not in serialized

    asyncio.run(run())


def test_failure_diagnostics_bound_cycles_and_omit_external_source_paths():
    from roma.services.live_acceptance_runner import failure_diagnostics

    private = 'sk-ant-private-caller-9876543210'
    try:
        exec(compile("raise ValueError('secret message')", '/private/' + private + '.py', 'exec'))
    except ValueError as error:
        error.__context__ = error
        diagnostics = failure_diagnostics(ExceptionGroup(
            private, [error, *(ValueError(private) for _ in range(100))]))
    assert len(diagnostics) == 16
    assert all(len(node['frames']) <= 8 for node in diagnostics)
    assert private not in json.dumps(diagnostics)
    assert 'secret message' not in json.dumps(diagnostics)


def test_observer_rejects_unreviewed_payload_before_logging():
    from roma.services.live_acceptance_runner import AcceptanceObserver
    emitted = []
    observer = AcceptanceObserver('r', emit=emitted.append)
    with pytest.raises(ValueError):
        observer.on_event('output_cleared', {'caller_text': 'private words'})
    assert not emitted and not observer.events


def test_acceptance_cannot_target_working_database():
    from roma.services.live_acceptance_runner import AcceptanceObserver
    observer = AcceptanceObserver('r')
    observer.expected_database = 'postgresql+asyncpg://roma@127.0.0.1/roma'
    with pytest.raises(ValueError):
        observer.require_target(observer.expected_database)


def test_check_mode_creates_no_evidence_or_budget(tmp_path, monkeypatch):
    from scripts import acceptance_live as cli
    called = []
    monkeypatch.setattr(cli, 'check', lambda options: called.append(options.language))
    monkeypatch.setattr(cli, 'EvidenceStore', lambda *a, **kw: pytest.fail('check must be read-only'))
    assert cli.main(['--check']) == 0 and called == ['gu']


def test_failed_cli_call_cleans_only_its_owned_demo_and_redis(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from pathlib import Path

    import redis
    from roma.repositories.postgres import demo_database
    from roma.services.live_acceptance_evidence import EvidenceStore
    from scripts import acceptance_live as cli
    from scripts import voice_prototype as prototype

    events = []
    platform_temporary_directory = cli.tempfile.TemporaryDirectory

    @contextmanager
    def platform_temporary_root(*args, **kwargs):
        assert 'dir' not in kwargs, "Use the platform temporary root, not a macOS path"
        with platform_temporary_directory(*args, **kwargs, dir=tmp_path) as directory:
            # Darwin's platform temp root can include a symlink. The owned DB
            # requires its canonical path, including when it runs on Linux.
            alias = tmp_path / 'platform-temp-alias'
            alias.symlink_to(directory, target_is_directory=True)
            try:
                yield str(alias)
            finally:
                alias.unlink()

    def start_owned_demo(root, **kwargs):
        assert root == Path(root).resolve()
        return 'postgresql+asyncpg://roma@127.0.0.1/roma_demo'

    monkeypatch.setattr(cli.tempfile, 'TemporaryDirectory', platform_temporary_root)
    class Process:
        pid = 123
        def poll(self):
            return None
        def terminate(self):
            events.append('terminate_redis')
        def wait(self, timeout=None):
            return 0
    class Redis:
        def __init__(self, **kwargs):
            pass
        def info(self, name):
            return {'process_id': 123}
        def close(self):
            pass
    monkeypatch.setattr(cli, 'check', lambda options: None)
    monkeypatch.setattr(cli, 'reserve_budget', lambda *a: 800)
    monkeypatch.setattr(cli, 'source_identity', lambda: {'source_commit': 'a' * 40})
    monkeypatch.setattr(demo_database, 'start_demo', start_owned_demo)
    monkeypatch.setattr(demo_database, 'stop_demo', lambda *a, **kw: events.append('stop_demo'))
    monkeypatch.setattr(cli.subprocess, 'Popen', lambda *a, **kw: Process())
    monkeypatch.setattr(redis, 'Redis', Redis)
    monkeypatch.setenv('DATABASE_URL', 'working-sentinel')
    async def fail(args):
        assert os.environ['DATABASE_URL'].endswith('/roma_demo')
        assert os.environ['ANTHROPIC_CALL_SPEND_CAP_INR'] == '2'
        assert args.half_duplex is False and args.persist_transcripts is False
        raise ValueError('private failure details')
    monkeypatch.setattr(prototype, 'local_run', fail)
    store = EvidenceStore(tmp_path / 'private')
    with pytest.raises(ValueError):
        cli.live(cli.parser().parse_args(['--yes-ready', '--no-rating']), store)
    assert events == ['terminate_redis', 'stop_demo']
    assert os.environ['DATABASE_URL'] == 'working-sentinel'
    assert store.read('session')['payload']['status'] == 'failed'


def test_capture_recovery_is_recorded_without_audio_or_a_passed_gate(tmp_path):
    import asyncio

    from roma.services.live_acceptance_evidence import EvidenceStore
    from roma.services.live_acceptance_runner import AcceptanceObserver, run_session
    async def run():
        store = EvidenceStore(tmp_path / 'private')
        observer = AcceptanceObserver('recovered-run', emit=lambda value: None)
        async def invoke():
            observer.on_event('capture_recovered', {'recovery_count': 1})
        async def verify():
            return {'committed': False, 'restored': False}
        await run_session(invoke, verify, observer, store, {'source_commit': 'a' * 40})
        physical = store.read('physical')['payload']
        assert physical['events'][0]['event'] == 'capture_recovered'
        assert physical['events'][0]['recovery_count'] == 1
        assert not physical['full_gate_passed']
        with pytest.raises(ValueError):
            observer.on_event('capture_recovered', {'caller_text': 'private'})
    asyncio.run(run())


@pytest.mark.parametrize("booking", [
    {"committed": False, "restored": False},
    {"committed": True, "restored": False},
    {"committed": None, "restored": None},
])
def test_completed_call_without_sql_commit_and_restore_is_not_verified(tmp_path, booking):
    import asyncio

    from roma.services.live_acceptance_evidence import EvidenceStore
    from roma.services.live_acceptance_runner import AcceptanceObserver, run_session

    async def run():
        async def invoke():
            pass
        async def verify():
            return booking
        store = EvidenceStore(tmp_path / "private")
        await run_session(invoke, verify, AcceptanceObserver("test", emit=lambda _: None),
                          store, {"source_commit": "a" * 40})
        receipt = store.read("session")["payload"]
        assert receipt["status"] == "completed"
        assert receipt["booking_verified"] is False
    asyncio.run(run())


@pytest.mark.parametrize('active,pending', [(False, False), (True, True), (False, True)])
def test_persistent_native_stream_does_not_imply_active_playback(active, pending):
    import asyncio

    from roma.services.live_acceptance_runner import AcceptanceObserver

    class Device:
        _output = object()  # The persistent stream stays open between replies.
        playback_active = active
        output_pending = pending
        def _epoch(self):
            return 2
        async def clear(self):
            self.playback_active = False

    class Voice:
        _busy = True
        def __init__(self, speaker):
            self.speaker = speaker
        def claim_turn(self):
            return 2

    async def run():
        observer = AcceptanceObserver('persistent-output', emit=lambda value: None)
        device = observer.device_type(Device)()
        voice = observer.voice_type(Voice)(device)
        voice.claim_turn()
        await device.clear()
        assert observer.events[0]['was_playing'] is active
        assert observer.events[1]['had_output'] is pending
    asyncio.run(run())


@pytest.mark.parametrize('language,booking_request,correction', [
    ('gu', 'કાલે સાંજે પાંચ વાગ્યે વડોદરા બ્રાન્ચમાં મુલાકાત બુક કરો.',
     'ના, કાલે બપોરે ત્રણ વાગ્યે વડોદરા રાખો.'),
    ('hi', 'कल शाम पाँच बजे वडोदरा ब्रांच में मुलाकात बुक कीजिए।',
     'नहीं, कल दोपहर तीन बजे वडोदरा रखिए।'),
    ('en', 'Book a visit to the Vadodara branch tomorrow at 5pm.',
     'No, change the Vadodara visit to tomorrow at 3pm.'),
], ids=['gu', 'hi', 'en'])
def test_live_instructions_match_selected_language_and_distinct_booking_times(
        monkeypatch, capsys, language, booking_request, correction):
    from scripts import acceptance_live as cli

    class StopBeforeBudget(Exception):
        pass

    def stop(*args):
        raise StopBeforeBudget

    monkeypatch.setattr(cli, 'check', lambda options: None)
    monkeypatch.setattr(cli, 'reserve_budget', stop)
    options = cli.parser().parse_args(['--yes-ready', '--no-rating', '--language', language])
    with pytest.raises(StopBeforeBudget):
        cli.live(options, None)
    output = capsys.readouterr().out
    assert booking_request in output and correction in output
    assert 'tomorrow 5pm' in output and 'tomorrow 3pm' in output
    assert 'CAPTURE_STARTED' in output
    if language != 'gu':
        assert 'ના, કાલે' not in output


def test_observer_cue_changes_to_three_pm_without_implying_booking():
    from dataclasses import dataclass

    from roma.domain.conversation.state import CallState
    from roma.services.live_acceptance_runner import AcceptanceObserver

    @dataclass
    class Outcome:
        status: str = 'played'

    emitted = []
    observer = AcceptanceObserver('guidance', emit=emitted.append)
    state = CallState(call_sid='voice-lab-guidance')
    observer.on_result(Outcome(), state)
    assert len(emitted) == 1 and 'tomorrow 3pm' in emitted[0]
    assert 'explicitly confirm' in emitted[0] and 'if correct' in emitted[0]
    assert 'ના, કાલે' not in emitted[0]
    assert not state.appointment_id and not state.locked_slot and not state.lead_wants_out
