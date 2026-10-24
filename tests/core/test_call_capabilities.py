"""Fresh provider capabilities bind identity and shared stream admission."""

import asyncio

import pytest
from roma.core.call_capabilities import CallCapabilities, RedisStreamReplay

ACCOUNT = "AC" + "1" * 32
CALL = "CA" + "2" * 32


def test_capability_binds_purpose_identity_expiry_and_rejects_tampering():
    clock = [1000]
    signer = CallCapabilities("offline-secret", ACCOUNT, clock=lambda: clock[0])
    intent = signer.issue_answer("lead-token")
    assert signer.verify_answer(intent, "lead-token") is None
    with pytest.raises(ValueError):
        signer.verify_answer(intent, "other-lead")
    with pytest.raises(ValueError):
        signer.verify_stream(intent, CALL, "lead-token")
    stream = signer.issue_stream(CALL, "lead-token")
    signer.verify_stream(stream, CALL, "lead-token")
    for token, call, lead in [
        (stream + "x", CALL, "lead-token"),
        (stream, "CA" + "3" * 32, "lead-token"),
        (stream, CALL, None),
    ]:
        with pytest.raises(ValueError):
            signer.verify_stream(token, call, lead)
    clock[0] = 1120
    with pytest.raises(ValueError):
        signer.verify_stream(stream, CALL, "lead-token")
    clock[0] = 1300
    with pytest.raises(ValueError):
        signer.verify_answer(intent, "lead-token")


def test_replay_is_shared_across_instances_and_redis_failure_is_closed():
    keys = set()

    class Redis:
        async def set(self, key, value, *, nx, ex):
            assert nx is True and ex == 600
            if key in keys:
                return False
            keys.add(key)
            return True

    async def run():
        first, second = RedisStreamReplay(client=Redis()), RedisStreamReplay(client=Redis())
        assert await first.consume(ACCOUNT, CALL)
        assert not await second.consume(ACCOUNT, CALL)

    asyncio.run(run())


def test_capabilities_reject_invalid_config_identity_and_future_issued_tokens():
    with pytest.raises(ValueError):
        CallCapabilities("secret", "invalid-account")
    with pytest.raises(ValueError):
        CallCapabilities("", ACCOUNT)
    token = CallCapabilities("secret", ACCOUNT, clock=lambda: 2000).issue_stream(CALL, None)
    with pytest.raises(ValueError):
        CallCapabilities("secret", ACCOUNT, clock=lambda: 1999).verify_stream(token, CALL, None)


def test_redis_replay_errors_propagate_without_local_fallback():
    class Redis:
        async def set(self, *_args, **_kwargs):
            raise RuntimeError("unavailable")

    with pytest.raises(RuntimeError):
        asyncio.run(RedisStreamReplay(client=Redis()).consume(ACCOUNT, CALL))


def test_redis_replay_admission_is_atomic_for_concurrent_workers():
    class Redis:
        def __init__(self):
            self.seen = set()

        async def set(self, key, _value, *, nx, ex):
            await asyncio.sleep(0)
            if key in self.seen:
                return None
            self.seen.add(key)
            return True

    async def run():
        redis = Redis()
        stores = [RedisStreamReplay(client=redis) for _ in range(20)]
        results = await asyncio.gather(*(store.consume(ACCOUNT, CALL) for store in stores))
        assert sum(results) == 1

    asyncio.run(run())
