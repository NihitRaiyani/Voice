"""The five-minute call budget (roma.controller.pacing).

Roma books a visit; she does not counsel. The reference corpus of real Weltec counselling
sessions runs 17–42 minutes, and none of that belongs on an outbound booking call — so the
call has a ceiling, enforced in code rather than asked for in a prompt.
"""

from roma.controller.pacing import (
    CLOSE_SECS,
    HURRY_SECS,
    OVER_SECS,
    PACING_LINES,
    band,
    pacing_line,
    should_force_pivot,
)


def test_a_fresh_call_is_open_and_says_nothing_about_time():
    assert band(0.0) == "open"
    assert pacing_line(0.0) == ""


def test_each_boundary_is_inclusive_at_the_start_of_its_band():
    """A band that started a tenth of a second late would be untestable and unpinnable."""
    assert band(HURRY_SECS - 0.1) == "open"
    assert band(HURRY_SECS) == "hurry"
    assert band(CLOSE_SECS - 0.1) == "hurry"
    assert band(CLOSE_SECS) == "close"
    assert band(OVER_SECS - 0.1) == "close"
    assert band(OVER_SECS) == "over"


def test_the_bands_are_ordered_in_time():
    assert 0 < HURRY_SECS < CLOSE_SECS < OVER_SECS


def test_a_wildly_overrun_call_stays_over_rather_than_wrapping():
    assert band(10_000.0) == "over"


def test_negative_elapsed_is_treated_as_the_start_of_the_call():
    """A monotonic clock cannot go backwards, but a mis-injected `elapsed_fn` can. The
    failure mode that matters is the one that ENDS a call early; reading a negative as
    "open" fails safe."""
    assert band(-5.0) == "open"


def test_every_band_has_a_line_and_only_open_is_empty():
    assert set(PACING_LINES) == {"open", "hurry", "close", "over"}
    assert PACING_LINES["open"] == ""
    for key in ("hurry", "close", "over"):
        assert PACING_LINES[key].strip(), key


def test_the_lines_are_instructions_not_a_stopwatch_reading():
    """ "elapsed: 214s" tells the model a number and no decision. Each line has to say what
    to DO, which is why they are imperative Hinglish rather than telemetry."""
    for key in ("hurry", "close", "over"):
        assert "TIME:" in PACING_LINES[key]
        assert "%" not in PACING_LINES[key] and "{" not in PACING_LINES[key]


def test_the_urgent_bands_push_towards_the_booking_or_the_exit():
    assert "slot" in PACING_LINES["hurry"]
    assert "slot" in PACING_LINES["close"] or "WhatsApp" in PACING_LINES["close"]
    assert "sign off" in PACING_LINES["over"]


def test_pacing_line_matches_the_band():
    for elapsed in (0.0, HURRY_SECS, CLOSE_SECS, OVER_SECS):
        assert pacing_line(elapsed) == PACING_LINES[band(elapsed)]


def test_no_pivot_is_forced_while_the_call_is_open():
    for phase in ("p2_discover", "p3_value", "p4_structure"):
        assert should_force_pivot(HURRY_SECS - 1, phase) is False


def test_discovery_and_the_pitch_are_cut_short_once_the_clock_bites():
    for elapsed in (HURRY_SECS, CLOSE_SECS, OVER_SECS):
        for phase in ("p2_discover", "p3_value", "p4_structure"):
            assert should_force_pivot(elapsed, phase) is True, (elapsed, phase)


def test_an_objection_is_never_abandoned_half_answered():
    """docs/03: evading a question a lead has actually asked is what loses a warm lead.
    Cutting P6 off mid-answer to talk about slots is exactly that, and no amount of time
    pressure makes it the right move."""
    assert should_force_pivot(OVER_SECS, "p6_objection") is False


def test_the_opening_is_never_skipped():
    """Without the inquiry confirm there is nothing to book, and pivoting at a lead who has
    not yet agreed to talk is how a call becomes a complaint."""
    assert should_force_pivot(OVER_SECS, "p1_open") is False


def test_the_close_is_left_alone():
    assert should_force_pivot(OVER_SECS, "p7_close") is False
    assert should_force_pivot(OVER_SECS, "p5_pivot") is False
