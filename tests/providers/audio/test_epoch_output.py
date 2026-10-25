"""Epoch-gated native output teardown with fake speakers only."""

import asyncio
import threading

import pytest
from roma.core.turn_epoch import TurnEpoch
from roma.providers.ai.contracts import AudioFormat, TTSResult
from roma.providers.audio.local_device import LocalAudioDevice


class OutputBackend:
    def __init__(self):
        self.events = []
        self.entered = threading.Event()
        self.release = threading.Event()
        self.created = 0

    def RawOutputStream(self, **kwargs):
        backend, index = self, self.created
        self.created += 1

        class Stream:
            def start(self):
                backend.events.append((index, "start"))

            def write(self, pcm):
                backend.events.append((index, "write"))
                if index == 0:
                    backend.entered.set()
                    backend.release.wait(2)
                return False

            def stop(self):
                backend.events.append((index, "stop"))

            def abort(self):
                backend.events.append((index, "abort"))
                backend.release.set()

            def close(self):
                backend.events.append((index, "close"))

        return Stream()


def test_clear_aborts_inflight_native_write_and_old_epoch_never_writes_again():
    async def run():
        backend, epochs = OutputBackend(), TurnEpoch()
        device = LocalAudioDevice(backend=backend, epoch_guard=epochs)
        old = epochs.snapshot()
        task = asyncio.create_task(
            device.play(TTSResult(b"\0\0" * 4000, AudioFormat(8000), turn_epoch=old))
        )
        assert await asyncio.to_thread(backend.entered.wait, 1)
        epochs.advance()
        await device.clear()
        assert epochs.current == old + 1  # clear owns no global epoch mutation.
        with pytest.raises(asyncio.CancelledError):
            await task
        assert backend.events.count((0, "write")) == 1
        assert backend.events.count((0, "abort")) == backend.events.count((0, "close")) == 1
        before = list(backend.events)
        with pytest.raises(asyncio.CancelledError):
            await device.play(TTSResult(b"\0\0" * 800, AudioFormat(8000), turn_epoch=old))
        assert backend.events == before
        await device.play(
            TTSResult(b"\0\0" * 800, AudioFormat(8000), turn_epoch=epochs.current)
        )
        assert (1, "write") in backend.events
        await device.aclose()

    asyncio.run(run())
