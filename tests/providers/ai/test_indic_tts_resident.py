import asyncio
import json
import os
import sys

import pytest
from roma.providers.ai.contracts import ProviderUnavailable, TTSRequest
from roma.providers.ai.indic_tts import IndicTTSProvider
from tests.providers.ai.test_indic_tts import manifest


def resident_provider(tmp_path, *, timeout=10, behavior=""):
    events = tmp_path / "events.jsonl"
    interpreter = tmp_path / "synthetic-runtime"
    interpreter.write_text(
        "#!" + sys.executable + "\n"
        "import json, os, pathlib, sys, time\n"
        f"events = pathlib.Path({str(events)!r})\n"
        "for line in sys.stdin.buffer:\n"
        " p=json.loads(line)\n"
        " with events.open('a') as f: f.write(json.dumps({'pid':os.getpid(),'op':p['op'],'id':p['id']})+'\\n')\n"
        f" {behavior or 'pass'}\n"
        " if p['op']=='synthesize': pathlib.Path(p['output']).write_bytes(bytes(320))\n"
        " print(json.dumps({'id':p['id'],'status':'ready' if p['op']=='load' else 'ok','turn_epoch':p['turn_epoch']}),flush=True)\n"
    )
    interpreter.chmod(0o700)
    return IndicTTSProvider(
        manifest_path=str(manifest(tmp_path)),
        python_path=str(interpreter),
        timeout_secs=timeout,
    ), events


def test_warmup_and_first_turn_keep_one_loaded_worker(tmp_path):
    async def run():
        provider, events = resident_provider(tmp_path)
        before = await provider.cache_identity(TTSRequest("नमस्ते"))
        await provider.warmup()
        try:
            after = await provider.cache_identity(TTSRequest("नमस्ते"))
            assert before != after
            result = await provider.synthesize(TTSRequest("नमस्ते", turn_epoch=7))
            again = await provider.synthesize(TTSRequest("धन्यवाद"))
            assert result.audio == again.audio == bytes(320)
            assert result.turn_epoch == 7
            rows = [json.loads(line) for line in events.read_text().splitlines()]
            assert [row["op"] for row in rows] == [
                "load",
                "synthesize",
                "synthesize",
                "synthesize",
            ]
            assert len({row["pid"] for row in rows}) == 1
            assert len({row["id"] for row in rows}) == 4
            pid = rows[0]["pid"]
        finally:
            await provider.aclose()
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)

    asyncio.run(run())


def test_native_resident_protocol_loads_once_for_warmup_and_sentences(monkeypatch):
    import io

    from roma.providers.ai import _indic_tts_worker as worker
    from roma.providers.ai._indic_tts_protocol import RESIDENT_PROFILE

    loaded, spoken = [], []
    model = object()

    def load(payload):
        loaded.append(payload)
        return model

    def speak(synth, payload, *, resident=False):
        assert synth is model and resident
        spoken.append(payload["text"])

    monkeypatch.setattr(worker, "_load_synth", load, raising=False)
    monkeypatch.setattr(worker, "_synthesize", speak, raising=False)
    stream = io.BytesIO(
        b"".join(
            json.dumps(p).encode() + b"\n"
            for p in [
                {
                    "id": 1,
                    "turn_epoch": None,
                    "op": "load",
                    "paths": {},
                    "runtime_distribution": "TTS",
                    "runtime_version": "0.22.0",
                    "voice": "female",
                    "profile": RESIDENT_PROFILE,
                },
                {
                    "id": 2,
                    "turn_epoch": 3,
                    "op": "synthesize",
                    "text": "नमस्ते",
                    "sample_rate": 8000,
                    "max_audio_secs": 30,
                    "output": "/tmp/synthetic.pcm",
                },
                {
                    "id": 3,
                    "turn_epoch": 3,
                    "op": "synthesize",
                    "text": "धन्यवाद",
                    "sample_rate": 8000,
                    "max_audio_secs": 30,
                    "output": "/tmp/synthetic.pcm",
                },
            ]
        )
    )
    output = io.BytesIO()
    worker.serve(stream, output)
    assert len(loaded) == 1 and spoken == ["नमस्ते", "धन्यवाद"]
    assert [json.loads(line)["status"] for line in output.getvalue().splitlines()] == [
        "ready",
        "ok",
        "ok",
    ]


def test_resident_restarts_when_verified_runtime_file_changes(tmp_path):
    async def run():
        provider, events = resident_provider(tmp_path)
        await provider.warmup()
        try:
            identity = await provider.cache_identity(TTSRequest("नमस्ते"))
            runtime = type(events)(provider.python_path)
            runtime.write_text(runtime.read_text() + "# reviewed runtime changed\n")
            assert await provider.cache_identity(TTSRequest("नमस्ते")) != identity
            await provider.synthesize(TTSRequest("नमस्ते"))
            rows = [json.loads(line) for line in events.read_text().splitlines()]
            assert len({r["pid"] for r in rows}) == 2
            assert [r["op"] for r in rows].count("load") == 2
        finally:
            await provider.aclose()

    asyncio.run(run())


def test_cancel_retains_bounded_admission_until_worker_reaped(tmp_path, monkeypatch):
    from pathlib import Path

    class Process:
        returncode = None

        def __init__(self):
            self.stdout = asyncio.StreamReader()
            self.stdin = self
            self.slow = asyncio.Event()
            self.reaping = asyncio.Event()
            self.reaped = asyncio.Event()
            self.release = asyncio.Event()

        def write(self, frame):
            p = json.loads(frame)
            if p.get("text") == "slow":
                self.slow.set()
                return
            if p["op"] == "synthesize":
                Path(p["output"]).write_bytes(bytes(320))
            self.stdout.feed_data(
                json.dumps(
                    {
                        "id": p["id"],
                        "status": "ready" if p["op"] == "load" else "ok",
                        "turn_epoch": p["turn_epoch"],
                    }
                ).encode()
                + b"\n"
            )

        async def drain(self):
            pass

        def kill(self):
            self.returncode = -9
            self.stdout.feed_eof()

        async def wait(self):
            self.reaping.set()
            await self.release.wait()
            self.reaped.set()

    async def run():
        processes = []

        async def create(*args, **kwargs):
            process = Process()
            processes.append(process)
            if len(processes) > 1:
                process.release.set()
            return process

        monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
        provider = IndicTTSProvider(
            manifest_path=str(manifest(tmp_path)), python_path=sys.executable
        )
        await provider.warmup()
        process = processes[0]
        active = asyncio.create_task(provider.synthesize(TTSRequest("slow")))
        await process.slow.wait()
        waiting = asyncio.create_task(provider.synthesize(TTSRequest("नमस्ते")))
        await asyncio.sleep(0)
        try:
            with pytest.raises(ProviderUnavailable, match="busy"):
                await asyncio.wait_for(provider.synthesize(TTSRequest("धन्यवाद")), 0.05)
        finally:
            active.cancel()
            await process.reaping.wait()
            active.cancel()  # Repeated cancellation must still retain the slot.
            await asyncio.sleep(0)
            assert len(processes) == 1 and not waiting.done()
            process.release.set()
            with pytest.raises(asyncio.CancelledError):
                await active
            await waiting
            assert process.reaped.is_set() and len(processes) == 2
            await provider.aclose()

    asyncio.run(run())


@pytest.mark.parametrize(
    "behavior",
    [
        "if p.get('text')=='bad': print(json.dumps({'id':p['id']+1,'status':'ok'}),flush=True); continue",
        "if p.get('text')=='bad': print('x'*65537,flush=True); continue",
        "if p.get('text')=='bad': print(json.dumps({'id':p['id'],'status':'ok','text':'private'}),flush=True); continue",
        "if p.get('text')=='bad': os._exit(7)",
        "if p.get('text')=='bad': pathlib.Path(p['output']).write_bytes(b'x'); print(json.dumps({'id':p['id'],'status':'ok','turn_epoch':p['turn_epoch']}),flush=True); continue",
        "if p.get('text')=='bad': pathlib.Path(p['output']).write_bytes(bytes(480002)); print(json.dumps({'id':p['id'],'status':'ok','turn_epoch':p['turn_epoch']}),flush=True); continue",
        "if p.get('text')=='bad': pathlib.Path(p['output']).symlink_to(events); print(json.dumps({'id':p['id'],'status':'ok','turn_epoch':p['turn_epoch']}),flush=True); continue",
    ],
)
def test_bad_reply_or_pcm_is_sanitized_reaped_and_next_request_reloads(tmp_path, behavior):
    async def run():
        provider, events = resident_provider(tmp_path, behavior=behavior)
        await provider.warmup()
        pid = json.loads(events.read_text().splitlines()[0])["pid"]
        with pytest.raises(ProviderUnavailable) as error:
            await provider.synthesize(TTSRequest("bad"))
        assert "bad" not in str(error.value) and "private" not in str(error.value)
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
        try:
            assert (await provider.synthesize(TTSRequest("नमस्ते"))).audio == bytes(320)
            rows = [json.loads(line) for line in events.read_text().splitlines()]
            assert len({row["pid"] for row in rows}) == 2
        finally:
            await provider.aclose()

    asyncio.run(run())


def test_timeout_reaps_before_return_and_next_request_reloads(tmp_path):
    from roma.providers.reliability import ProviderFailure, provider_budget

    async def run():
        provider, events = resident_provider(
            tmp_path, behavior="if p.get('text')=='slow': time.sleep(2)"
        )
        await provider.warmup()
        pid = json.loads(events.read_text().splitlines()[0])["pid"]
        with provider_budget(0.03):
            with pytest.raises(ProviderFailure, match="timeout"):
                await provider.synthesize(TTSRequest("slow"))
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
        try:
            await provider.synthesize(TTSRequest("नमस्ते"))
            assert len({json.loads(row)["pid"] for row in events.read_text().splitlines()}) == 2
        finally:
            await provider.aclose()

    asyncio.run(run())


def test_stale_epoch_discards_resident_output_and_reaps(tmp_path):
    from roma.core.turn_epoch import TurnEpoch, epoch_scope

    async def run():
        provider, events = resident_provider(
            tmp_path, behavior="if p.get('text')=='slow': time.sleep(.1)"
        )
        await provider.warmup()
        pid = json.loads(events.read_text().splitlines()[0])["pid"]
        guard = TurnEpoch()
        with epoch_scope(guard, guard.current):
            pending = asyncio.create_task(
                provider.synthesize(TTSRequest("slow", turn_epoch=guard.current))
            )
        async with asyncio.timeout(5):
            while len(events.read_text().splitlines()) < 3:  # noqa: ASYNC110 -- process boundary polling.
                await asyncio.sleep(0.005)
        guard.advance()
        with pytest.raises(asyncio.CancelledError):
            await pending
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
        await provider.aclose()

    asyncio.run(run())


def test_voice_and_manifest_changes_replace_one_worker(tmp_path):
    async def run():
        provider, events = resident_provider(tmp_path)
        await provider.warmup()
        try:
            await provider.synthesize(TTSRequest("नमस्ते", voice="male"))
            path = type(events)(provider.manifest_path)
            data = json.loads(path.read_text())
            data["runtime_version"] = "0.22.1"
            path.write_text(json.dumps(data))
            await provider.synthesize(TTSRequest("नमस्ते", voice="male"))
            rows = [json.loads(row) for row in events.read_text().splitlines()]
            assert [r["op"] for r in rows].count("load") == 3
            pids = list(dict.fromkeys(r["pid"] for r in rows))
            assert len(pids) == 3
            for pid in pids[:-1]:
                with pytest.raises(ProcessLookupError):
                    os.kill(pid, 0)
        finally:
            await provider.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("sample_rate", [8000, 16000])
@pytest.mark.parametrize("frames", [1, 2, 20, 240, 1600])
def test_cosine_fade_is_finite_keeps_frames_and_has_exact_15ms_edges(sample_rate, frames):
    import math

    from roma.providers.ai._indic_tts_worker import _edge_fade

    samples = [1.0] * frames
    _edge_fade(samples, sample_rate)
    assert len(samples) == frames and all(math.isfinite(s) and 0 <= s <= 1 for s in samples)
    edge = min(round(sample_rate * 0.015), frames // 2)
    if edge:
        assert samples[0] == samples[-1] == 0
    assert samples[edge : frames - edge] == [1.0] * (frames - 2 * edge)
    assert samples == list(reversed(samples))


def test_protocol_frames_have_strict_ids_and_64k_total_bound():
    from roma.providers.ai._indic_tts_protocol import decode_frame, encode_frame

    for data in [b"{}\n", b'{"id":true}\n', b'{"id":0}\n', b'{"id":1}', b"x" * 65537 + b"\n"]:
        with pytest.raises(ValueError):
            decode_frame(data)
    with pytest.raises(ValueError):
        encode_frame({"id": 1, "text": "x" * 65536})


def test_unwarmed_oneshot_preserves_internal_epoch(tmp_path):
    async def run():
        interpreter = tmp_path / "oneshot-runtime"
        interpreter.write_text(
            "#!"
            + sys.executable
            + '\nimport json,pathlib,sys\np=json.loads(sys.stdin.read())\npathlib.Path(p["output"]).write_bytes(bytes(320))\n'
        )
        interpreter.chmod(0o700)
        provider = IndicTTSProvider(
            manifest_path=str(manifest(tmp_path)), python_path=str(interpreter)
        )
        result = await provider.synthesize(TTSRequest("नमस्ते", turn_epoch=4))
        assert result.turn_epoch == 4

    asyncio.run(run())


def test_wrong_epoch_reply_is_discarded_and_worker_reaped(tmp_path):
    async def run():
        provider, events = resident_provider(
            tmp_path,
            behavior="if p.get('text')=='bad': pathlib.Path(p['output']).write_bytes(bytes(320)); print(json.dumps({'id':p['id'],'status':'ok','turn_epoch':999}),flush=True); continue",
        )
        await provider.warmup()
        pid = json.loads(events.read_text().splitlines()[0])["pid"]
        with pytest.raises(ProviderUnavailable):
            await provider.synthesize(TTSRequest("bad", turn_epoch=2))
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
        await provider.aclose()

    asyncio.run(run())


def test_close_drains_bounded_stdout_before_reaping(tmp_path, monkeypatch):
    from pathlib import Path

    class Process:
        returncode = None

        def __init__(self):
            self.stdin = self
            self.stdout = asyncio.StreamReader()

        def write(self, frame):
            p = json.loads(frame)
            if p["op"] == "synthesize":
                Path(p["output"]).write_bytes(bytes(320))
            self.stdout.feed_data(
                json.dumps(
                    {
                        "id": p["id"],
                        "status": "ready" if p["op"] == "load" else "ok",
                        "turn_epoch": p["turn_epoch"],
                    }
                ).encode()
                + b"\n"
            )

        async def drain(self):
            pass

        def kill(self):
            self.returncode = -9
            self.stdout.feed_eof()

        async def wait(self):
            assert self.stdout.at_eof(), "unread pipe can prevent subprocess reaping"

    async def run():
        process = Process()

        async def create(*args, **kwargs):
            return process

        monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
        provider = IndicTTSProvider(
            manifest_path=str(manifest(tmp_path)), python_path=sys.executable
        )
        await provider.warmup()
        process.stdout.feed_data(b"x" * 200000)
        await provider.aclose()

    asyncio.run(run())


def test_warmup_waits_for_inflight_unwarmed_worker(tmp_path, monkeypatch):
    from pathlib import Path

    class Process:
        returncode = None

        def __init__(self):
            self.stdin = self
            self.stdout = asyncio.StreamReader()
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def communicate(self, payload):
            self.started.set()
            await self.release.wait()
            Path(json.loads(payload)["output"]).write_bytes(bytes(320))  # noqa: ASYNC240 -- tiny synthetic PCM.
            self.returncode = 0
            self.stdout.feed_eof()

        def write(self, frame):
            p = json.loads(frame)
            if p["op"] == "synthesize":
                Path(p["output"]).write_bytes(bytes(320))
            self.stdout.feed_data(
                json.dumps(
                    {
                        "id": p["id"],
                        "status": "ready" if p["op"] == "load" else "ok",
                        "turn_epoch": p["turn_epoch"],
                    }
                ).encode()
                + b"\n"
            )

        async def drain(self):
            pass

        def kill(self):
            self.returncode = -9
            self.stdout.feed_eof()

        async def wait(self):
            pass

    async def run():
        processes = []

        async def create(*args, **kwargs):
            process = Process()
            processes.append(process)
            return process

        monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
        provider = IndicTTSProvider(
            manifest_path=str(manifest(tmp_path)), python_path=sys.executable
        )
        cold = asyncio.create_task(provider.synthesize(TTSRequest("नमस्ते")))
        while not processes:  # noqa: ASYNC110 -- process boundary polling.
            await asyncio.sleep(0.001)
        await processes[0].started.wait()
        warm = asyncio.create_task(provider.warmup())
        try:
            await asyncio.sleep(0.02)
            assert len(processes) == 1 and not warm.done()
        finally:
            processes[0].release.set()
            await asyncio.gather(cold, warm)
            await provider.aclose()

    asyncio.run(run())


def test_close_propagates_cancellation_only_after_native_reaping():
    class Process:
        returncode = None
        stdout = None

        def __init__(self):
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.reaped = False

        def kill(self):
            self.returncode = -9

        async def wait(self):
            self.started.set()
            await self.release.wait()
            self.reaped = True

    async def run():
        provider = IndicTTSProvider()
        process = Process()
        provider._resident_process = process
        closing = asyncio.create_task(provider.aclose())
        await process.started.wait()
        closing.cancel()
        await asyncio.sleep(0)
        assert not closing.done()
        process.release.set()
        with pytest.raises(asyncio.CancelledError):
            await closing
        assert process.reaped

    asyncio.run(run())
