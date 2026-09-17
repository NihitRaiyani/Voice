"""Do-not-call / consent registry (docs/07 §consent — DND scrubbing).

`docs/07` requires the dialer to respect India's DND registry before placing a
call. Real TRAI DND/DLT scrubbing needs Weltec's registered provider, which is
not wired at Gate 0. So this is the Gate-0 STUB, and it is honest about that:

  Posture = ALLOWLIST-ONLY. A number may be dialed only if it is on an explicit
  consented-lead allowlist. Unknown numbers are BLOCKED — the strict reading of
  the Gate-0 rule "never dial unguarded" (docs/07 fail-safe). A separate
  suppression set always wins, so a number that is both consented and suppressed
  is still blocked.

Step 1 (telephony spine) injects the real consented-lead source; the real TRAI
registry replaces StubRegistry via the DoNotCallRegistry protocol without
touching the gate. This is NOT the TRAI registry — do not treat it as one.
"""

import re
from typing import Protocol, runtime_checkable

_NON_DIGIT = re.compile(r"\D")


def normalize_phone(phone: str) -> str:
    """Reduce a number to its comparison key: the last 10 digits (Indian mobile).

    Strips spaces, dashes, and the +91 / 91 / 0 prefixes so that '9876543210',
    '+91 98765 43210', and '098765-43210' all compare equal. A shorter string is
    returned digits-only rather than padded — membership just won't match, which
    fails safe (unknown → blocked).
    """
    digits = _NON_DIGIT.sub("", phone)
    return digits[-10:] if len(digits) >= 10 else digits


_E164 = re.compile(r"^\+[1-9]\d{7,14}$")


def is_e164(phone: str) -> bool:
    """Whether `phone` is a well-formed E.164 number.

    SHAPE ONLY. It says nothing about whether the number may be dialled — that is
    `DoNotCallRegistry.is_dialable`, and the two must never be confused: a perfectly formed
    number that nobody consented to is exactly the case Gate 0 exists to refuse.

    Lives here rather than at the HTTP edge because this is where phone-number shape is
    already reasoned about (`normalize_phone` above), and a second notion of "valid number"
    in a route handler is how the two drift apart.
    """
    return bool(phone and _E164.match(phone.strip()))


# India's mobile range: +91 then ten digits opening 6-9. Landlines, short codes and every
# non-India country code are outside it.
_INDIAN_MOBILE = re.compile(r"^\+91[6-9]\d{9}$")


def is_indian_mobile(phone: str) -> bool:
    """Whether `phone` is an Indian mobile number in E.164.

    SHAPE ONLY, like `is_e164` — it says nothing about whether the number may be dialled.

    Narrower than E.164 on purpose: **Roma is not a general dialer.** Every lead is an Indian
    mobile from Weltec's enquiry list, so a `+1` number reaching the dial path is a mistake,
    and the cheapest place to find that out is a 400 rather than an international leg on the
    bill. It is also the last shape check standing now that the consent allowlist is gone.
    """
    return bool(phone and _INDIAN_MOBILE.match(phone.strip()))


def numbers_from_config(raw: str) -> "set[str]":
    """Parse a comma-separated phone list from config into a set.

    Was `consented_from_config`, renamed 2026-08-04 when the web endpoint stopped using an
    allowlist: it now feeds a DENYLIST (`DND_NUMBERS`), and a name promising consent on a
    suppression list is the kind of thing that gets misread in a hurry.

    Parsing is unchanged. What the caller does with the set is the whole difference, which is
    why the two registries below are separate classes rather than one with a flag.
    """
    return {part.strip() for part in (raw or "").split(",") if part.strip()}


@runtime_checkable
class DoNotCallRegistry(Protocol):
    """A number is dialable only if the registry positively clears it."""

    def is_dialable(self, phone: str) -> bool: ...


class StubRegistry:
    """Allowlist-only Gate-0 stub. NOT the TRAI DND registry.

    `consented`: numbers explicitly cleared to dial (any format — normalized here).
    `suppressed`: do-not-call numbers; always wins over `consented`.
    Empty sets (the default) block everything — the safe default.
    """

    def __init__(
        self,
        consented: "set[str] | None" = None,
        suppressed: "set[str] | None" = None,
    ) -> None:
        self._consented = {
            normalize_phone(p) for p in (consented or set())
        }  # set of phone numbers that have given consent
        self._suppressed = {
            normalize_phone(p) for p in (suppressed or set())
        }  # set of phone numbers that should never be called

    def is_dialable(self, phone: str) -> bool:
        key = normalize_phone(phone)
        if key in self._suppressed:
            return False
        return key in self._consented


class DenylistRegistry:
    """ALLOW-BY-DEFAULT. A number is dialable unless it is explicitly suppressed.

    **This is the opposite posture to `StubRegistry` above, deliberately, and it is weaker.**
    Read that class's docstring first: allowlist-only, unknown numbers blocked, "never dial
    unguarded" (docs/07 fail-safe). That remains correct and remains what
    `scripts/place_test_call.py` uses.

    This exists because the web endpoint stopped using a consent allowlist on 2026-08-04. The
    allowlist was a TESTING safeguard — the production consent basis is Weltec's enquiry list
    — and editing `.env` plus restarting per test number was friction with no safety value.
    Removing it flips Gate 0 to allow-by-default for that path, so `decisions.md` records the
    trade and this class states it in the open rather than hiding under a docstring that
    promises the opposite.

    **An empty denylist blocks nothing.** That is the shipped default and it is honest: with
    no TRAI/DLT feed wired (docs/07 §consent), we have no real suppression data, and
    pretending otherwise would be worse than saying so. What bounds the endpoint instead is
    the calling window, the spend cap, the hourly dial cap, bearer auth and the localhost
    bind — not this.

    Same `DoNotCallRegistry` protocol, so the real TRAI registry still replaces either class
    without the gate changing.
    """

    def __init__(self, suppressed: "set[str] | None" = None) -> None:
        self._suppressed = {normalize_phone(p) for p in (suppressed or set())}

    def is_dialable(self, phone: str) -> bool:
        return normalize_phone(phone) not in self._suppressed


__all__ = [
    "DoNotCallRegistry",
    "StubRegistry",
    "DenylistRegistry",
    "normalize_phone",
    "is_e164",
    "is_indian_mobile",
    "numbers_from_config",
]
