import asyncio

import pytest


def test_old_turn_is_rejected_and_independent_call_is_unaffected():
    from roma.core.turn_epoch import TurnEpoch, epoch_scope, require_current_epoch

    first, independent = TurnEpoch(), TurnEpoch()
    old = first.snapshot()
    with epoch_scope(first, old):
        require_current_epoch()
        assert first.advance() > old
        with pytest.raises(asyncio.CancelledError):
            require_current_epoch()
    assert independent.is_current(independent.snapshot())
    require_current_epoch()  # Legacy paths do not acquire a synthetic epoch.
