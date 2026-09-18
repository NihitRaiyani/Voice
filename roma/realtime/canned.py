"""Pre-rendered canned audio for Step 1 (docs/10: "prove audio in/out with a
hardcoded canned line, still behind the filter").

TTS (Bulbul) isn't wired until Step 3, so Step 1 speaks pre-rendered 8kHz μ-law
clips. Two lines are spoken on connect, in order:

  1. the recording-consent disclosure (`CONSENT_LINE` from the pre-dial gate), and
  2. a short canned greeting proving audio-out end-to-end.

The filter contract still holds on the TEXT: every line is run through
`safe_output()` at load, and if the filter would alter it we refuse to serve the
audio (a bad line must never reach the wire). The .ulaw assets are produced offline
by `scripts/make_canned_clip.py`; the committed clips are PLACEHOLDER test audio
until the approved TEST recording is dropped in. Never point these at a real lead
with placeholder consent wording (docs/decisions.md Open).
"""

import logging
from dataclasses import dataclass
from pathlib import Path

from roma.domain.calls import CONSENT_LINE
from roma.domain.safety import safe_output
from roma.realtime.ulaw import ulaw_to_pcm16

_log = logging.getLogger("roma.realtime")

_ASSETS = Path(__file__).parent / "assets"

# THE inbound opener. Played from disk the instant the socket connects — no LLM, no TTS
# round trip.
#
# Roma is inbound: someone dialled Weltec and is holding the phone to their ear waiting to
# hear that it rang through. The cold first LLM turn measures 3.28s TTFB (CAfe5a00b) plus
# synthesis on top, and three seconds of nothing after you ring a business is when a caller
# says "hello? hello?" and hangs up. This line cannot be generated; it has to already exist.
#
# It is deliberately TWO WORDS OF CONTENT. An answerer says "Hello, Weltec Institute" and
# stops — the caller rang with something to say and the only thing in their way is us still
# talking. Everything else Roma might have opened with ("kaise help kar sakti hoon", a pitch,
# a question) is over-speaking on a turn that belongs to them. It is also why the asset is
# under a second: shorter line, smaller file, less to interrupt.
OPENING_LINE = "Hello, Weltec Institute."

SAMPLE_RATE = 8000


@dataclass(frozen=True)
class CannedLine:
    text: str
    pcm: bytes


def _load(text: str, asset: str) -> CannedLine:
    """Enforce the filter on the text, then decode the μ-law asset to PCM16."""
    screened = safe_output(text)
    if screened != text:
        raise ValueError(
            f"canned line would be altered by the pre-TTS filter; refusing to "
            f"serve it as audio (asset={asset!r})"
        )
    path = _ASSETS / asset
    if not path.exists():
        raise FileNotFoundError(
            f"canned audio asset missing: {path}. Generate it with scripts/make_canned_clip.py."
        )
    return CannedLine(text=text, pcm=ulaw_to_pcm16(path.read_bytes()))


def consent_line() -> CannedLine:
    """The recording-consent line, spoken first on every call."""
    return _load(CONSENT_LINE, "consent.ulaw")


def opening_line() -> CannedLine:
    """The inbound opener, played at connect. Rendered by scripts/make_opening_clip.py."""
    return _load(OPENING_LINE, "opening.ulaw")


__all__ = [
    "CannedLine",
    "consent_line",
    "opening_line",
    "OPENING_LINE",
    "SAMPLE_RATE",
]
