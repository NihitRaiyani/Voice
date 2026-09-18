"""The two opening flags, and the call that proved one flag could not carry both.

## The failure

Call 0ce455b0 (2026-08-04) ran five minutes and thirteen seconds. Roma said nothing at all
and heard nothing at all. The teardown line:

    media stream ended: inbound_frames=923 ... outbound_frames=0 outbound_bytes=0
                        phase=p1_open won=False

`outbound_frames=0` means the serializer was never handed a single frame to play — not one
byte of audio reached the carrier for the whole call. Meanwhile, six times:

    opening guard: held a transcript (48 chars) — Roma still opening

The lead was talking. The transcripts were arriving. Every one was held for an opening that
had already been cancelled, by a greeter that had been told to stand down.

## The cause

`media.py` computed ONE boolean, `opening_is_canned`, and gave it to two processors that
were asking different questions:

  * `PickupGreeter` reads it as "someone else already spoke the opener, stand down."
  * `OpeningTurnGuard` reads it as "the opener emits no `BotStoppedSpeakingFrame`, so start
    open or you will hold transcripts forever."

For an inbound canned opener both answers are `True`, which is why it worked for weeks. A
PRE-RENDERED OUTBOUND opener is the case where they diverge: nothing has spoken it (the
greeter owns it), but it is raw audio (the guard must start open). One flag said the wrong
thing to each processor, and the two wrongs did not cancel — they compounded into a call
that neither spoke nor listened.

Worse, the assignment `opening_is_canned = opening_is_canned or bool(opener_pcm)` sat ~100
lines BELOW where the guard was constructed, so the value never reached the processor its
own comment was written about, and silently reached the one it broke.

## Why these tests exist when the others did not catch it

`test_opener_cache.py` and `test_opening_guard.py` both pin this behaviour already — by
handing the processors literal booleans. They prove each processor is correct WHEN TOLD
CORRECTLY. Nothing proved anything told it. `test_opener_cache.py` even carries a test
whose docstring says "this test is here because reading that comment was not enough" — and
it too passes a literal `True`.

A wiring bug needs a test of the wiring, so the decision now lives in a pure function.
"""

from roma.realtime.opening import OpeningTurnGuard, PickupGreeter, opening_posture

# --- the truth table --------------------------------------------------------------------


def test_the_pre_rendered_outbound_opener_is_the_row_where_the_answers_diverge():
    """THE regression. Call 0ce455b0: five silent minutes.

    Not spoken by anyone (the greeter must play it) but raw audio (the guard must not wait
    for a frame it will never see). Any implementation that returns the same value for both
    fields fails here, which is the whole reason this file exists.
    """
    posture = opening_posture(
        is_outbound=True, has_canned_line=True, has_prerendered_opener=True
    )
    assert posture.already_spoken is False, (
        "nothing has spoken this opener — PickupGreeter OWNS it. Standing down means it is "
        "never played at all: outbound_frames=0 for the entire call"
    )
    assert posture.opener_is_raw_audio is True, (
        "a pre-rendered clip is OutputAudioRawFrame and emits no BotStoppedSpeakingFrame, "
        "so a guard left shut holds every transcript for the rest of the call"
    )
    assert posture.already_spoken != posture.opener_is_raw_audio, (
        "this row is the entire justification for two flags instead of one"
    )


def test_inbound_answers_yes_to_both():
    """Where the old single flag was accidentally correct, and why this shipped.

    Inbound, `canned.opening_line()` is played at connect: it HAS been spoken, and it is raw
    audio. Both answers are True, one flag sufficed, and it worked for weeks.
    """
    posture = opening_posture(
        is_outbound=False, has_canned_line=True, has_prerendered_opener=False
    )
    assert posture.already_spoken is True
    assert posture.opener_is_raw_audio is True


def test_outbound_without_a_cached_clip_answers_no_to_both():
    """The pre-cache path, unchanged: the greeter synthesizes from the template, and that
    generated utterance DOES emit BotStoppedSpeakingFrame, so the guard may hold."""
    posture = opening_posture(
        is_outbound=True, has_canned_line=True, has_prerendered_opener=False
    )
    assert posture.already_spoken is False
    assert posture.opener_is_raw_audio is False


def test_an_outbound_call_never_stands_the_greeter_down_whatever_else_is_true():
    """`already_spoken` is about the INBOUND connect-time play and nothing else.

    On an outbound call the callee speaks first and we deliberately queue nothing at connect,
    so there is no configuration in which something else has already greeted. If this ever
    goes True for outbound, the greeter goes silent and so does the call.
    """
    for has_canned in (True, False):
        for has_prerendered in (True, False):
            posture = opening_posture(
                is_outbound=True,
                has_canned_line=has_canned,
                has_prerendered_opener=has_prerendered,
            )
            assert posture.already_spoken is False, (
                f"outbound must never stand the greeter down "
                f"(canned={has_canned}, prerendered={has_prerendered})"
            )


def test_a_raw_audio_opener_always_opens_the_guard():
    """The inverse invariant. Any opener that is bytes rather than a generated utterance
    must leave the guard open, however it was obtained — this is the property the guard
    actually depends on, and naming the flag for it is what makes that checkable."""
    for is_outbound, has_canned, has_prerendered in (
        (False, True, False),  # inbound canned
        (True, True, True),  # outbound pre-rendered
    ):
        posture = opening_posture(
            is_outbound=is_outbound,
            has_canned_line=has_canned,
            has_prerendered_opener=has_prerendered,
        )
        assert posture.opener_is_raw_audio is True


# --- the flags still mean what the processors read them as -------------------------------


def test_the_posture_actually_configures_the_processors_as_intended():
    """Ties the table to the objects, so a rename cannot quietly re-cross the wires.

    The bug was not that either processor misbehaved — both were correct in isolation and
    fully tested. It was that the values arrived swapped. This asserts the end state for the
    row that broke: guard OPEN, greeter ARMED.
    """
    posture = opening_posture(
        is_outbound=True, has_canned_line=True, has_prerendered_opener=True
    )
    guard = OpeningTurnGuard(
        opener_is_raw_audio=posture.opener_is_raw_audio, enable_direct_mode=True
    )
    greeter = PickupGreeter(
        opening_already_spoken=posture.already_spoken,
        opening_audio_fn=lambda: b"\x00\x01" * 100,
        enable_direct_mode=True,
    )

    assert guard.opened is True, "the guard must start open or every transcript is held"
    assert greeter._opening_already_spoken is False, (
        "the greeter must stay armed or the pre-rendered opener is never played"
    )
