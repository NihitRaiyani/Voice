"""Epoch-gated callback output with fake speakers only."""

import asyncio

import pytest
from roma.core.turn_epoch import TurnEpoch
from roma.providers.ai.contracts import AudioFormat, TTSResult
from roma.providers.audio.local_device import LocalAudioDevice
from tests.providers.audio.test_callback_output import CallbackSpeakerBackend


def test_clear_flushes_callback_queue_and_old_epoch_never_hands_off_again():
    async def run():
        backend, epochs = CallbackSpeakerBackend(), TurnEpoch()
        device = LocalAudioDevice(backend=backend, epoch_guard=epochs)
        old = epochs.snapshot()
        task = asyncio.create_task(
            device.play(TTSResult(b"\1\0" * 8000, AudioFormat(8000), turn_epoch=old))
        )
        try:
            assert await asyncio.to_thread(backend.writing.wait, 1)
            epochs.advance()
            await device.clear()
            assert epochs.current == old + 1
            with pytest.raises(asyncio.CancelledError):
                await task
            handed = sum(any(block) for block in backend.blocks)
            assert backend.events == ["output_start"]
            assert backend.blocks[-1] == bytes(640)
            with pytest.raises(asyncio.CancelledError):
                await device.play(TTSResult(b"\1\0" * 800, AudioFormat(8000), turn_epoch=old))
            assert sum(any(block) for block in backend.blocks) == handed
            await device.play(TTSResult(b"\2\0" * 800, AudioFormat(8000), turn_epoch=epochs.current))
            assert backend.created == 1
            assert sum(any(block) for block in backend.blocks) > handed
        finally:
            await asyncio.gather(task, return_exceptions=True)
            await device.aclose()
        assert backend.events == ["output_start", "output_abort", "output_close"]

    asyncio.run(run())
