"""The conversation fixture: what the replay harness replays (docs/10 Step 7).

Step 7 says "replay harness against the transcripts". There are no transcripts. Nothing
persists a turn-by-turn record today — `TranscriptionLogger.finals` dies with the
websocket, and `PostcallJob` deliberately carries none ("No PII ... no transcript"). So
the corpus is hand-written conversation scripts, and real-call replay lands when
consent sign-off unblocks storing what a lead actually said.

That is not a downgrade. A fixture states its EXPECTATION — which phase this turn should
land in, which slot should be filled — and an expectation is exactly what a captured
transcript lacks. A recorded call tells you what happened; only a fixture tells you what
should have happened.

Format: JSONL, one object per line, stdlib `json` only (no YAML dependency for four
files). Line 1 is the header, every line after it is a turn:

    {"name": "...", "description": "...", "lead_name": "ji", "branch": "Vadodara",
     "expect_slots": {"education": "BCom"}, "expect_outcome": "locked"}
    {"lead": "haan bolo", "roma": "...", "expect_phase": "p2_discover", "slot": "..."}

`lead` is what the lead said (what STT would have returned). `roma` is the reference line
Roma is expected to speak — offline it stands in for the model, and it is what the word-cap
and filter checks read; live, the model's real line replaces it and the reference becomes
the thing the model is compared against.

`expect_phase` is the phase the machine must land on AFTER this turn.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

TURN_KEYS = frozenset(
    {
        "lead",
        "roma",
        "expect_phase",
        "slot",
        "accept",
        "confirm",
        "accept_hour",
        "accept_period",
        "chose_offer",
        "elapsed",
        "note",
    }
)
HEADER_KEYS = frozenset(
    {
        "name",
        "description",
        "lead_name",
        "branch",
        "expect_slots",
        "expect_outcome",
        "expect_win",
    }
)


class ScriptError(ValueError):
    """A malformed fixture. Raised at load, never mid-run.

    A typo'd key here is the failure mode that matters most: `expect_phse` would silently
    assert nothing and the harness would report a confident green over a check that never
    ran. Unknown keys are therefore an error, not a warning.
    """


@dataclass(frozen=True)
class Turn:
    """One lead utterance and the expectation attached to it."""

    lead: str
    roma: str = ""
    expect_phase: str = ""
    slot: "str | None" = None
    accept: bool = False
    confirm: bool = False
    accept_hour: "int | None" = None
    accept_period: str = ""
    chose_offer: "int | None" = None
    elapsed: float = 0.0
    note: str = ""


@dataclass(frozen=True)
class Script:
    """A whole scripted call plus its end-state expectations."""

    name: str
    turns: tuple[Turn, ...]
    description: str = ""
    # Default "ji" keeps the seven pre-inbound fixtures behaving as a resumed call with the
    # name already captured. A fixture that wants to exercise capture sets `"lead_name": null`
    # explicitly — see inbound_name_capture.jsonl.
    lead_name: "str | None" = "ji"
    branch: str = "Vadodara"
    expect_slots: dict = field(default_factory=dict)
    expect_outcome: str = ""
    expect_win: "bool | None" = None
    path: "Path | None" = None

    @property
    def roma_lines(self) -> tuple[str, ...]:
        return tuple(t.roma for t in self.turns if t.roma)


def _check_keys(obj: dict, allowed: frozenset, where: str) -> None:
    unknown = set(obj) - allowed
    if unknown:
        raise ScriptError(
            f"{where}: unknown key(s) {sorted(unknown)}; allowed {sorted(allowed)}"
        )


def loads(text: str, *, path: "Path | None" = None) -> Script:
    """Parse fixture text. Blank lines and `#` comment lines are skipped."""
    lines = [
        ln for ln in (raw.strip() for raw in text.splitlines()) if ln and not ln.startswith("#")
    ]
    if not lines:
        raise ScriptError(f"{path or '<text>'}: empty script")

    try:
        header = json.loads(lines[0])
    except json.JSONDecodeError as exc:
        raise ScriptError(f"{path or '<text>'} line 1: {exc}") from exc
    if not isinstance(header, dict):
        raise ScriptError(f"{path or '<text>'} line 1: header must be an object")
    _check_keys(header, HEADER_KEYS, f"{path or '<text>'} line 1")
    if "name" not in header:
        raise ScriptError(f"{path or '<text>'} line 1: header needs a 'name'")

    turns = []
    for i, ln in enumerate(lines[1:], start=2):
        try:
            obj = json.loads(ln)
        except json.JSONDecodeError as exc:
            raise ScriptError(f"{path or '<text>'} line {i}: {exc}") from exc
        if not isinstance(obj, dict):
            raise ScriptError(f"{path or '<text>'} line {i}: turn must be an object")
        _check_keys(obj, TURN_KEYS, f"{path or '<text>'} line {i}")
        if "lead" not in obj:
            raise ScriptError(f"{path or '<text>'} line {i}: turn needs a 'lead'")
        turns.append(Turn(**obj))

    if not turns:
        raise ScriptError(f"{path or '<text>'}: header but no turns")

    return Script(turns=tuple(turns), path=path, **header)


def load(path: "str | Path") -> Script:
    p = Path(path)
    return loads(p.read_text(encoding="utf-8"), path=p)


def load_dir(directory: "str | Path") -> list[Script]:
    """Every `*.jsonl` in `directory`, sorted by filename for a stable report order."""
    d = Path(directory)
    if not d.is_dir():
        raise ScriptError(f"{d}: not a directory")
    scripts = [load(p) for p in sorted(d.glob("*.jsonl"))]
    if not scripts:
        raise ScriptError(f"{d}: no *.jsonl scripts found")
    return scripts


__all__ = ["Script", "Turn", "ScriptError", "load", "load_dir", "loads"]
