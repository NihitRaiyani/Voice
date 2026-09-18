from datetime import UTC, datetime

from roma.dialer.window import (
    CALL_TIMEZONE,
    in_calling_window,
)


def _ist(hour, minute=0):
    """A naive wall-clock time treated as IST by in_calling_window."""
    return datetime(2026, 7, 24, hour, minute)


def test_midday_is_inside_window():
    assert in_calling_window(_ist(14)) is True


def test_before_start_is_outside():
    assert in_calling_window(_ist(8, 59)) is False


def test_at_or_after_end_is_outside():
    assert in_calling_window(_ist(21, 0)) is False
    assert in_calling_window(_ist(22, 30)) is False


def test_start_boundary_is_inside_end_boundary_is_outside():
    assert in_calling_window(_ist(9, 0)) is True
    assert in_calling_window(_ist(20, 59)) is True
    assert in_calling_window(_ist(21, 0)) is False


def test_aware_non_ist_now_is_judged_in_ist():
    utc_1700 = datetime(2026, 7, 24, 17, 0, tzinfo=UTC)
    assert utc_1700.astimezone(CALL_TIMEZONE).hour == 22
    assert in_calling_window(utc_1700) is False

    utc_0600 = datetime(2026, 7, 24, 6, 0, tzinfo=UTC)
    assert in_calling_window(utc_0600) is True
