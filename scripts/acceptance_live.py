#!/usr/bin/env python3
"""One guided TEST ONLY local full-duplex acceptance run. No model downloads."""
import argparse
import asyncio
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from roma.services.live_acceptance_evidence import (  # noqa: E402
    EvidenceStore,
    private_file,
    update_readiness,
)
from roma.services.live_acceptance_runner import (  # noqa: E402
    AcceptanceObserver,
    reserve_budget,
    run_session,
)


def source_identity():
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    digest = hashlib.sha256()
    for folder in ('roma', 'scripts'):
        for path in sorted((ROOT / folder).rglob('*.py')):
            digest.update(str(path.relative_to(ROOT)).encode() + b'\0' + path.read_bytes())
    return {'source_commit': commit, 'source_tree_sha256': digest.hexdigest(),
            'working_tree_dirty': bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT))}


@contextmanager
def environment(values):
    old = {name: os.environ.get(name) for name in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for name, value in old.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def speech_environment(language):
    return {'STT_PROVIDER': 'whisper_mlx', 'STT_LANGUAGE': language,
            'WHISPER_MLX_MODEL_DIR': str(ROOT / 'var/roma/models/whisper-large-v3-turbo-mlx-4bit'),
            'INDIC_TTS_MANIFEST': str(ROOT / 'var/roma/indic-tts/manifest.json'),
            'INDIC_TTS_PYTHON': str(ROOT / 'var/roma/tts-runtime/bin/python'),
            'TTS_PROVIDER': 'indic_tts', 'TTS_VOICE': 'female', 'LLM_PROVIDER': 'claude',
            'ANTHROPIC_MODEL': 'claude-haiku-5-5', 'ANTHROPIC_CALL_SPEND_CAP_INR': '2',
            'ANTHROPIC_MAX_RETRIES': '0', 'ANTHROPIC_MAX_PROMPT_BYTES': '16384',
            'ANTHROPIC_CALL_TOKEN_BUDGET': '64000', 'HF_HUB_OFFLINE': '1',
            'TRANSFORMERS_OFFLINE': '1', 'NLTK_DATA': str(ROOT / 'var/roma/nltk_data'),
            'RECORDING_ENABLED': 'false', 'APP_ENV': 'test'}


def voice_args(options, observer=None):
    from scripts import voice_prototype as prototype
    args = prototype.parser().parse_args(['--mode', 'local', '--allow-microphone',
        '--allow-paid-api', '--allow-database-write', '--max-turns', '8'])
    args.input_device, args.output_device = options.input_device, options.output_device
    args.speech_transport = getattr(options, "speech_transport", "in_process")
    args.acceptance_observer = observer
    return args


def check(options):
    from roma.providers.audio.echo import WebRTCEchoCanceller
    from scripts import voice_prototype as prototype
    with environment(speech_environment(options.language)):
        args = voice_args(options)
        cfg, _, tts_cfg = prototype.local_preflight(args)
        from contextlib import AsyncExitStack

        from roma.providers.ai.contracts import TTSRequest
        async def validate_tts():
            async with AsyncExitStack() as cleanup:
                _, tts = await prototype.local_speech_providers(args, cfg, tts_cfg, cleanup)
                await tts.cache_identity(TTSRequest("नमस्ते", language="hi"))
        asyncio.run(validate_tts())
        WebRTCEchoCanceller(stream_delay_ms=0).preflight()
        if shutil.which('redis-server') is None:
            raise ValueError('Native Redis is missing')
    print('CHECK_OK: cached speech/AEC/config prerequisites. No microphone, inference or paid request opened.', flush=True)


async def verify_booking(url, sid):
    from roma.domain.conversation.state import CallState
    from roma.repositories.postgres.booking import PostgresBookingStore
    from roma.services.booking_service import BookingService
    from roma.services.booking_session import BookingSession
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    engine = create_async_engine(url, pool_size=1, max_overflow=0)
    try:
        store = PostgresBookingStore(async_sessionmaker(engine, expire_on_commit=False))
        call = await store.call(sid=sid) if sid else None
        active = await store.active_for_call(call.id) if call else None
        restored = CallState(call_sid=sid or '', branch='Vadodara')
        if call:
            await BookingSession(BookingService(store), restored).restore()
        return {'committed': active is not None,
                'restored': active is not None and restored.appointment_id == str(active.id)}
    finally:
        await engine.dispose()


def collect_rating(store, *, listener, language, ask=input):
    session = store.read('session')
    while True:
        value = ask('Naturalness 1–5 (or skip; no default score): ').strip()
        if value.casefold() == 'skip':
            return
        try:
            score = float(value)
            if not 1 <= score <= 5:
                raise ValueError
            break
        except ValueError:
            print('Enter an actual score from 1 to 5, or skip.')
    correctness = ask('Correctness 1–5 (or skip): ').strip()
    notes = ask('Brief listening notes, no personal details: ').strip()
    backchannel = ask('Did Roma continue through your short haan/okay? yes/no/unsure: ').strip().casefold()
    interruption = ask('Did Roma stop and use the changed time? yes/no/unsure: ').strip().casefold()
    payload = {'source_commit': session['source_commit'], 'run_id': session['payload']['run_id'],
               'listener': listener, 'language': language, 'naturalness': score,
               'notes': notes, 'backchannel_preserved_reported': {'yes': True, 'no': False}.get(backchannel),
               'interruption_worked_reported': {'yes': True, 'no': False}.get(interruption)}
    if correctness.casefold() != 'skip':
        payload['correctness'] = float(correctness)
    store.write('voice_rating', payload)


def live(options, store):
    from roma.repositories.postgres.demo_database import start_demo, stop_demo
    from scripts import voice_prototype as prototype
    check(options)
    print('TEST ONLY prepared booking scenario; discovery was prefilled with fictional details. '
          'No caller audio/transcripts retained. Software metrics do not prove acoustic <50ms.\n'
          '1: Ask in Gujarati for Vadodara visit options: વડોદરા બ્રાન્ચમાં મુલાકાત માટે સમય બતાવો.\n'
          '2: Say one short haan/okay during a response. Observe whether it cuts off.\n'
          '3: During the next response change to tomorrow 5pm: ના, કાલે સાંજે પાંચ વાગ્યે વડોદરા રાખો.\n'
          'After the readback explicitly confirm ONLY if the time is correct. Extra confirmation is allowed. '
          'Wait for CAPTURE_STARTED before speaking.', flush=True)
    if not options.yes_ready and input('Type ready when at the microphone/speakers: ').strip().casefold() != 'ready':
        raise ValueError('No ready indication')
    total = reserve_budget(ROOT / 'var/roma/ops-20261024/acceptance-session-reservations.jsonl', options.language)
    print(f'Aggregate conservative reservations: INR{total / 100:.2f}/50; this launch capped at INR2.', flush=True)
    observer = AcceptanceObserver(uuid4().hex, emit=lambda value: print(value, flush=True))
    source = source_identity()
    with tempfile.TemporaryDirectory(prefix='roma-live-', dir='/private/tmp') as temporary:
        root = Path(temporary)
        os.chmod(root, 0o700)
        database, redis_process = None, None
        try:
            database = start_demo(root / 'db', app_env='test')
            observer.expected_database = database
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
            redis_process = subprocess.Popen(['redis-server', '--bind', '127.0.0.1', '--port', str(port),
                '--save', '', '--appendonly', 'no', '--dir', str(root)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            import redis
            client = redis.Redis(host='127.0.0.1', port=port, socket_timeout=1, socket_connect_timeout=1)
            try:
                for _ in range(30):
                    if redis_process.poll() is not None:
                        raise ValueError('Owned Redis did not start')
                    try:
                        if client.info('server')['process_id'] != redis_process.pid:
                            raise ValueError('Redis ownership differs')
                        break
                    except redis.ConnectionError:
                        time.sleep(.1)
                else:
                    raise ValueError('Owned Redis readiness failed')
            finally:
                client.close()
            values = {**speech_environment(options.language), 'DATABASE_URL': database,
                      'REDIS_URL': f'redis://127.0.0.1:{port}/0', 'ROMA_DATA_DIR': str(root / 'runtime')}
            with environment(values):
                async def invoke():
                    await asyncio.wait_for(prototype.local_run(voice_args(options, observer)), 400)
                async def verify():
                    return await verify_booking(database, observer.sid)
                booking = asyncio.run(run_session(invoke, verify, observer, store, source))
            print('SQL_BOOKING_CHECK=' + json.dumps(booking), flush=True)
        finally:
            if redis_process is not None:
                redis_process.terminate()
                try:
                    redis_process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    redis_process.kill()
                    redis_process.wait(timeout=5)
            if database is not None:
                stop_demo(root / 'db', app_env='test')
    print('MICROPHONE_CLOSED. Signed physical/turn-budget receipts saved.', flush=True)
    if not options.no_rating:
        collect_rating(store, listener=options.listener, language='hi-Hinglish')
    update_readiness(ROOT, store)


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    modes = p.add_mutually_exclusive_group()
    modes.add_argument('--check', action='store_true')
    modes.add_argument('--governance', type=Path)
    modes.add_argument('--record-rating', action='store_true')
    modes.add_argument('--update-readiness', action='store_true')
    p.add_argument('--language', choices=('gu', 'hi', 'en'), default='gu')
    p.add_argument('--listener', default='Owner', help='Pseudonymous actual listener label')
    p.add_argument('--speech-transport', choices=('in_process', 'local_http'), default='in_process')
    p.add_argument('--input-device', type=int)
    p.add_argument('--output-device', type=int)
    p.add_argument('--yes-ready', action='store_true', help='Supervisor already received an explicit ready indication')
    p.add_argument('--no-rating', action='store_true', help='Collect the actual score separately after microphone closes')
    return p


def main(argv=None):
    options = parser().parse_args(argv)
    try:
        if options.check:
            check(options)
            return 0
        store = EvidenceStore(ROOT / 'var/acceptance')
        if options.governance:
            payload = json.loads(private_file(options.governance))
            store.write('governance', {**payload, 'source_commit': source_identity()['source_commit']})
            print('Confirmed governance metadata recorded; broader erasure/legal scope remains explicit.')
        elif options.record_rating:
            collect_rating(store, listener=options.listener, language='hi-Hinglish')
            update_readiness(ROOT, store)
        elif options.update_readiness:
            update_readiness(ROOT, store)
        else:
            live(options, store)
        return 0
    except (KeyboardInterrupt, asyncio.CancelledError):
        print('Acceptance interrupted. No gate passed; reservations retained.', file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001 — fixed category, never private provider details.
        from roma.core.voice_errors import map_voice_error
        print('Acceptance did not pass: ' + map_voice_error(exc).message, file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
