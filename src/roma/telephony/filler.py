"""The filler token — the documented way to hide LLM latency (docs/05, docs/02, docs/06).

docs/02: "the instant endpointing fires, play a 200-300ms cached 'achha…' / 'haan ji…'
while the LLM spins up. Perceived response ≈ 250ms even if the pipeline took ~900ms."

## Why this is the right lever

Measured on live call CA9c5f7cf: OpenAI TTFB **1.21s**, Bulbul TTFB **0.46s**, against an
endpoint wait of 0.85s. The LLM alone costs more than the turn-final knob docs/10 Step 7 set
out to tune. Turning 850ms down cannot fix a 1.21s model round trip — and cutting it too far
fires VAD stop inside the 0.3-0.5s natural pauses that are 52% of the corpus, which makes
the call *worse*.

So the budget closes by HIDING latency, not eliminating it (docs/02 says exactly this). The
lead hears "achha…" ~250ms after they stop, and Roma's real sentence arrives behind it. The
dead air is what makes a voice agent feel broken; the total is barely noticed.

## Always cached, never generated

docs/06: "always cached, never live-generated". A filler that waits on a TTS round trip has
become the latency it exists to hide. These are μ-law assets rendered once by
`scripts/make_filler_clips.py` and decoded at app build, so the call path is a dict lookup.

## Missing assets degrade, they do not crash

Unlike the consent line — where a missing asset MUST be fatal, because a call without stated
consent is not allowed to happen — a missing filler is a latency regression and nothing
more. It logs once at build and the call proceeds unmasked.
"""

import itertools
import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from roma.telephony.ulaw import ulaw_to_pcm16

_log = logging.getLogger("roma.telephony")

_ASSETS = Path(__file__).parent / "assets"

SAMPLE_RATE = 8000

FILLER_LINES: dict[str, str] = {
    "achha": "Achha…",
    "theek_hai": "Theek hai…",
    # "hmm": "Hmm…" — REPLACED 2026-08-01 after the first outbound call. Heard live it comes
    # out as "umm…", which is not an acknowledgement but a hesitation: it makes Roma sound
    # like she is stalling for an answer rather than confirming she heard one. The other
    # fillers are words a counsellor actually says; this one was a noise. docs/05 asks for a
    # cached "achha…"/"haan ji…" and never for a filled pause.
    #
    # Replaced rather than deleted: the pool must stay >= 4 or half-rate play still sounds
    # like one word (CA3c7d3c7b). "Samjhi…" is neutral like the rest — it acknowledges
    # hearing without taking the stance "Haan ji…" takes — and it is the feminine form, which
    # every word Roma says about herself has to be.
    "samjhi": "Samjhi…",
    "ji": "Ji…",
}

# Intent-matched fillers. The neutral pool ACKNOWLEDGES; these respond to the SHAPE of the
# turn — a question deserves "dekhiye…" (I'm about to answer), an objection "samajh rahi
# hoon…" (I heard the concern), and "achha…" at either reads as Roma agreeing with something
# she is about to push back on.
#
# TWO clips per bucket, not one. The first cut shipped one, on the theory that the anti-tic
# alternation in `_should_fill` would keep a bucket from repeating. It does not: alternation
# only blocks CONSECUTIVE turns, and on the 2026-08-08 call turns 10 and 12 were both
# objections, so "Samajh rahi hoon…" played twice inside fourteen seconds. A bucket of one
# is a cycle of one.
QUESTION_LINES: dict[str, str] = {
    "dekhiye": "Dekhiye…",
    "achha_ji": "Achha ji…",
}
OBJECTION_LINES: dict[str, str] = {
    # Longer than the 200-300ms docs/05 asks of an opener clip, accepted deliberately: an
    # objection turn's completion is the SLOWEST kind (P6 reframe), so there is more dead
    # air to cover, and empathy clipped short reads as dismissal.
    "samajh_rahi_hoon": "Samajh rahi hoon…",
    "haan_ji": "Haan ji…",
}


class FillerIntent(Enum):
    NEUTRAL = "neutral"
    QUESTION = "question"
    OBJECTION = "objection"


_INTENT_NAMES: dict[FillerIntent, frozenset] = {
    FillerIntent.QUESTION: frozenset(QUESTION_LINES),
    FillerIntent.OBJECTION: frozenset(OBJECTION_LINES),
}

# Question-shaped turns, when STT gives no "?" (Saaras usually does not). Interrogatives
# only — anything matching the objection classifier has already been routed before these
# are consulted, so overlap ("kitna" etc.) resolves to OBJECTION.
_QUESTION_CUES = frozenset(
    {
        "kya",
        "kaise",
        "kab",
        "kaun",
        "kaunsa",
        "kaunse",
        "kahan",
        "kyun",
        "kyu",
        "kitna",
        "kitni",
        "kitne",
        "क्या",
        "कैसे",
        "कब",
        "कौन",
        "कहाँ",
        "कहां",
        "क्यों",
        "कितना",
        "कितनी",
        "શું",
        "કેમ",
        "ક્યારે",
        "ક્યાં",
        "કેવી",
        "કેટલી",
    }
)


def intent_for(text: "str | None", phase: "str | None" = None) -> FillerIntent:
    """The turn-shape this filler should match. Never raises; unknown is NEUTRAL.

    THE PHASE OUTRANKS THE TEXT, because the phase is the machine's own decision and the
    text classifier is a guess about the same question. Two live mismatches on 2026-08-08
    forced this:

      * `dekhiye (question, p6_objection)` — the machine had already classified an
        objection and moved the phase; the filler still called it a question and Roma
        opened an objection turn with "Dekhiye…", which reads as a lecture, not empathy.
      * `dekhiye (question, p1_open)` — the lead's FIRST words after the greeting. P1 is
        two turns of hello; opening one with "Dekhiye…" is a stranger starting a lecture.
        P1 is pinned NEUTRAL whatever the words look like.

    Only where the machine has no opinion (P2-P5, P7) does the text decide.
    """
    try:
        if phase == "p1_open":
            return FillerIntent.NEUTRAL
        if phase == "p6_objection":
            return FillerIntent.OBJECTION
        if not text:
            return FillerIntent.NEUTRAL
        from roma.controller.objection import classify_objection
        from roma.guardrails.normalize import tokens

        if classify_objection(text) is not None:
            return FillerIntent.OBJECTION
        if "?" in text or set(tokens(text)) & _QUESTION_CUES:
            return FillerIntent.QUESTION
        return FillerIntent.NEUTRAL
    except Exception:  # noqa: BLE001 — a mask-selection failure must never cost the turn
        return FillerIntent.NEUTRAL


# Held-line clips, played when a turn has gone LONG — not when it starts.
#
# Different job from FILLER_LINES, which acknowledges what the lead just said. Eight seconds
# into a stall an acknowledgement is an answer arriving far too late, and reads worse than
# the silence. These say "I am still here, still working" and nothing else.
#
# Live calls acf8e78f and c5672a23 (2026-08-01) stalled 57s and 28s on one OpenAI request,
# with `keepCallAlive="true"` holding the line open the whole time. The lead heard a live
# channel with nobody on it and hung up. Both times.
HOLDING_LINES: dict[str, str] = {
    "ek_second": "Ek second…",
    "ek_minute": "Bas ek minute ji…",
}


FILLER_TARGET_RMS = 4200

# Full SENTENCES are leveled to Bulbul's own speaking level, not the filler level.
#
# Measured on the shipped assets: `opening.ulaw` (a real Bulbul sentence) sits at RMS 7280
# while the fillers sit at 4200 — deliberately, because a filler is an aside and should be
# quieter than the line it introduces. Rendering the phrase cache at 4200 gave every cached
# sentence a ~4.6 dB drop against Roma's live voice, so a substitution audibly changed
# volume mid-conversation. A cached line must be indistinguishable from a synthesized one.
SENTENCE_TARGET_RMS = 7280

_VOICED_FLOOR = 200


def level_to_target(pcm: bytes, target_rms: int = FILLER_TARGET_RMS) -> bytes:
    """Scale 16-bit PCM so its VOICED samples sit at `target_rms`. Never clips, never
    amplifies silence."""
    import array

    samples = array.array("h")
    samples.frombytes(pcm)
    voiced = [s for s in samples if abs(s) > _VOICED_FLOOR]
    if not voiced:
        return pcm
    rms = (sum(s * s for s in voiced) / len(voiced)) ** 0.5
    if rms <= 0:
        return pcm
    gain = target_rms / rms
    peak = max(abs(s) for s in samples)
    if peak * gain > 32000:
        gain = 32000 / peak
    out = array.array("h", (int(max(-32768, min(32767, s * gain))) for s in samples))
    return out.tobytes()


def filler_path(name: str) -> Path:
    return _ASSETS / f"filler_{name}.ulaw"


@dataclass(frozen=True)
class FillerClip:
    name: str
    text: str
    pcm: bytes

    @property
    def secs(self) -> float:
        return len(self.pcm) / 2 / SAMPLE_RATE


def load_holding() -> list[FillerClip]:
    """Decode the held-line clips (`HOLDING_LINES`). Same degrade-never-crash contract."""
    return _load(HOLDING_LINES, "holding")


def load_fillers() -> list[FillerClip]:
    """Decode every rendered filler — neutral pool plus intent buckets. Returns [] if none
    are present; a missing intent clip just leaves its bucket falling back to neutral.

    Called once at app build, never per call — the point is that the hot path does no I/O.
    """
    return _load({**FILLER_LINES, **QUESTION_LINES, **OBJECTION_LINES}, "filler")


def _load(lines: dict, kind: str) -> list[FillerClip]:
    clips = []
    for name, text in lines.items():
        path = filler_path(name)
        if not path.exists():
            continue
        try:
            clips.append(FillerClip(name=name, text=text, pcm=ulaw_to_pcm16(path.read_bytes())))
        except Exception:  # noqa: BLE001 — a bad filler must never stop a call from starting
            _log.warning("filler clip %s failed to load; skipping", path.name)
    if not clips:
        _log.warning(
            "no %s clips found in %s — turns will play unmasked LLM latency "
            "(run scripts/make_filler_clips.py). See docs/05.",
            kind,
            _ASSETS,
        )
    else:
        _log.info(
            "loaded %d %s clip(s): %s",
            len(clips),
            kind,
            ", ".join(f"{c.name}={c.secs:.2f}s" for c in clips),
        )
    return clips


class FillerPicker:
    """Round-robin over the loaded clips, bucketed by turn intent.

    Rotation, not random: `random` would repeat the same filler twice in a row often enough
    to be noticed, and on a six-turn call "achha… achha… achha…" is a worse artefact than
    the silence it replaced. One picker per call, so two concurrent calls do not share a
    cursor (docs/08). An intent whose bucket has no clips falls back to the neutral cycle,
    so a missing asset is a register regression, never silence.

    `played` counts clips that actually went downstream — the caller reports success via
    `note_played()` AFTER the push. The counter used to increment inside `next()`, which
    counted attempts: a push that failed still bumped it, so the teardown line could claim
    a mask the lead never heard.
    """

    def __init__(self, clips: "list[FillerClip] | None") -> None:
        self._clips = list(clips or [])
        by_intent: dict[FillerIntent, list[FillerClip]] = {
            FillerIntent.NEUTRAL: [],
            FillerIntent.QUESTION: [],
            FillerIntent.OBJECTION: [],
        }
        for clip in self._clips:
            for intent, names in _INTENT_NAMES.items():
                if clip.name in names:
                    by_intent[intent].append(clip)
                    break
            else:
                by_intent[FillerIntent.NEUTRAL].append(clip)
        self._cycles = {
            intent: itertools.cycle(bucket) if bucket else None
            for intent, bucket in by_intent.items()
        }
        self.played = 0

    def __bool__(self) -> bool:
        return bool(self._clips)

    def next(self, intent: FillerIntent = FillerIntent.NEUTRAL) -> "FillerClip | None":
        cycle = self._cycles.get(intent) or self._cycles[FillerIntent.NEUTRAL]
        if cycle is None:
            return None
        return next(cycle)

    def note_played(self) -> None:
        self.played += 1


__all__ = [
    "FillerClip",
    "FillerIntent",
    "HOLDING_LINES",
    "OBJECTION_LINES",
    "QUESTION_LINES",
    "load_holding",
    "FillerPicker",
    "FILLER_LINES",
    "SAMPLE_RATE",
    "filler_path",
    "intent_for",
    "load_fillers",
    "level_to_target",
    "FILLER_TARGET_RMS",
    "SENTENCE_TARGET_RMS",
    "strip_leading_ack",
]


_LEADING_ACKS = (
    "achha ji",
    "acha ji",
    "accha ji",
    "haan ji",
    "theek hai",
    "thik hai",
    "sahi hai",
    # WITH the pronoun, and first — matching is `startswith` over an ordered tuple, and
    # "samajh gayi" never fires on the line the model actually writes. Call 98aa06a3 played
    # the `samjhi` clip and then said "Main samajh gayi" on top of it, twice in consecutive
    # turns, which is the "accha accha" tic rebuilt out of a different word.
    "koi baat nahi, main samajh gayi",
    "koi baat nahi, main samjhi",
    "koi baat nahi",
    "main samajh gayi",
    "main samajh gaya",
    "main samjhi",
    "samjhi",
    "samajh gayi",
    "bilkul",
    "achha",
    "acha",
    "accha",
    "haan",
    "hmm",
    "ji",
)


def strip_leading_ack(line: str) -> str:
    """Drop a leading acknowledgement and its punctuation, if the line has one.

    Returns the line unchanged when removing the opener would leave nothing to say — a
    turn that is ONLY "Achha ji." is short, but it is still Roma's whole turn, and dropping
    it would hand the lead silence.
    """
    if not line:
        return line
    stripped = line.lstrip()
    lowered = stripped.casefold()
    for ack in _LEADING_ACKS:
        if not lowered.startswith(ack):
            continue
        rest = stripped[len(ack) :].lstrip(" ,.!—-–:;")
        if not rest:
            return line
        return rest[0].upper() + rest[1:] if rest[0].islower() else rest
    return line
