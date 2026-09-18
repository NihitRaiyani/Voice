"""Backchannel lexicon + endpointing thresholds for Layer 3 barge-in (docs/05).

Pure data and predicates — no pipecat import, so it unit-tests standalone. The
pipecat strategies that consume it live in `roma.realtime.turntaking`. This mirrors
the `guardrails/lexicon.py` vs `guardrails/filter.py` split: the words live apart from
the machinery that acts on them, because the words are what gets edited as calls come in.

docs/05's backchannel rule, verbatim: a user-speech span that is (a) < ~600ms AND
(b) in the backchannel lexicon AND (c) spoken while Roma is mid-utterance is NOT a
turn-take — "log it, keep talking". All three conditions, or it is a real barge-in.

**Gujarati script is first-class.** STT runs `gu-IN` (D3) and returns Gujarati, not
romanized text. A live call already stalled at P1 because `હા જી` was missing from an
affirmation lexicon; every lead-facing matcher here carries Gujarati spellings.

Matching is token-equality on `guardrails.normalize.tokens` — the same tokenizer the
guardrails filter and the P6 objection classifier use.
"""

from roma.domain.safety.normalize import tokens

# docs/05 Layer 2, from the 403-pooled-pause analysis. These are THE source values: the
# `Settings` fields mirror them and `.env` sets them explicitly on top.
#
# They previously read 0.8 / 0.3 / 0.55 / 1.10, which matched no document. The 0.55s default
# is what broke b5273f93: pause p50 is 0.48s and p75 0.66s, so 0.55 cuts inside more than
# half of natural pauses. Worse on this stack than the corpus implies — Sarvam's finals land
# ~1.0s after end-of-speech, so Roma began answering BEFORE the transcript of the turn she
# was answering arrived, and that late transcript then opened a NEW turn and interrupted her
# 183ms in. 850ms (pause p90) lets the final land first.
BACKCHANNEL_MAX_SECS = 0.60  # docs/05: a span under ~600ms over Roma is an ack, not a turn

# How many separate speech attempts over ONE bot turn mean "stop talking". The 600ms gate
# above only ever sees a single continuous span, so short repeated attempts slipped through
# it entirely — "wait" is ~300ms and each burst cancels the timer before it expires. Call
# b67b25da: the lead said "wait wait wait wait wait" and Roma talked over all five.
#
# Two, not three. One short sound while Roma speaks is the acknowledgement the gate above
# exists to protect; a second one in the same breath is not something anyone does by
# accident while being talked over.
BARGE_IN_ATTEMPTS = 2
ENDPOINT_TERMINAL_SECS = 0.50  # after a clear terminal answer ("haan", "Vadodara", "2025")
ENDPOINT_DEFAULT_SECS = 0.85  # pause p90; the number docs/05 calls "the core number"
ENDPOINT_CONTINUATION_SECS = 1.30  # after a continuation marker ("matlab…", "ek minute…")

BACKCHANNEL_TOKENS: set[str] = {
    "haan",
    "haa",
    "han",
    "ha",
    "hanji",
    "haanji",
    "haji",
    "ji",
    "jee",
    "jii",
    "jihan",
    "jihaan",
    "hmm",
    "hmmm",
    "hm",
    "hun",
    "mm",
    "mhm",
    "mmhm",
    "huh",
    "achha",
    "acha",
    "accha",
    "achcha",
    "acchha",
    "हाँ",
    "हां",
    "हा",
    "जी",
    "जीहाँ",
    "हम्म",
    "हूँ",
    "हूं",
    "अच्छा",
    "હા",
    "હાં",
    "હાજી",
    "જી",
    "જીહા",
    "હમ્મ",
    "હં",
    "હૂં",
    "અચ્છા",
}

TERMINAL_TOKENS: set[str] = {
    "haan",
    "ha",
    "han",
    "ji",
    "yes",
    "yeah",
    "ok",
    "okay",
    "theek",
    "thik",
    "sahi",
    "bilkul",
    "pakka",
    "barabar",
    "chokkas",
    "done",
    "correct",
    "હા",
    "હાજી",
    "જી",
    "ઠીક",
    "બરાબર",
    "ચોક્કસ",
    "સાચું",
    "ખરું",
    "हाँ",
    "हां",
    "जी",
    "ठीक",
    "बराबर",
    "सही",
    "बिल्कुल",
    "na",
    "naa",
    "nahi",
    "nahin",
    "no",
    "nope",
    "ના",
    "નથી",
    "નહીં",
    "नहीं",
    "ना",
}

TERMINAL_PHRASES: list[list[str]] = [
    ["theek", "hai"],
    ["thik", "hai"],
    ["sahi", "hai"],
    ["ok", "hai"],
    ["ठीक", "है"],
    ["सही", "है"],
    ["बराबर", "है"],
    ["ઠીક", "છે"],
    ["બરાબર", "છે"],
    ["સાચું", "છે"],
    ["ચોક્કસ", "છે"],
    ["ha", "ji"],
    ["haan", "ji"],
    ["હા", "જી"],
    ["हाँ", "जी"],
]

TERMINAL_PLACES: set[str] = {
    "vadodara",
    "baroda",
    "ahmedabad",
    "amdavad",
    "surat",
    "rajkot",
    "anand",
    "nadiad",
    "bharuch",
    "gandhinagar",
    "vapi",
    "navsari",
    "વડોદરા",
    "બરોડા",
    "અમદાવાદ",
    "સુરત",
    "રાજકોટ",
    "આણંદ",
    "નડિયાદ",
    "भरूच",
    "वडोदरा",
    "अहमदाबाद",
}

CONTINUATION_TOKENS: set[str] = {
    "matlab",
    "actually",
    "basically",
    "yani",
    "yaani",
    "kyunki",
    "kyonki",
    "lekin",
    "but",
    "jaise",
    "etle",
    "मतलब",
    "यानी",
    "क्योंकि",
    "लेकिन",
    "મતલબ",
    "યાની",
    "કેમકે",
    "એટલે",
    "જેમકે",
}

CONTINUATION_PHRASES: list[list[str]] = [
    ["ek", "minute"],
    ["ek", "minit"],
    ["ek", "second"],
    ["ek", "sec"],
    ["એક", "મિનિટ"],
    ["एक", "मिनट"],
    ["haan", "to"],
    ["ha", "to"],
    ["han", "to"],
    ["હા", "તો"],
    ["हाँ", "तो"],
    ["हां", "तो"],
    ["thodi", "der"],
    ["ruko", "zara"],
    ["જરા", "ઊભા"],
    ["थोड़ी", "देर"],
]

_TAIL_TOKENS = 2  # reduce false positives


def is_backchannel(text: str) -> bool:
    """True iff every token is a backchannel word (and there is at least one).

    `haan haan` and `હા જી` qualify; `હા પણ મોંઘું છે` does not — one content word and
    the lead is taking the turn, not nodding along.
    """
    toks = tokens(text)
    if not toks:
        return False
    return all(t in BACKCHANNEL_TOKENS for t in toks)


def endpoint_timeout_for(
    text: str,
    *,
    terminal_secs: float = ENDPOINT_TERMINAL_SECS,
    default_secs: float = ENDPOINT_DEFAULT_SECS,
    continuation_secs: float = ENDPOINT_CONTINUATION_SECS,
) -> float:
    """Adaptive turn-final silence for `text` so far (docs/05 Layer 2).

    Continuation is checked BEFORE terminal, because `હા` is both an affirmation and the
    head of the continuation `હા તો`. Waiting 450ms too long is recoverable; cutting the
    lead off mid-sentence is not.

    The three values are overridable so docs/10 Step 7 can tune them from `Settings` on the
    production origin without editing this module. The defaults are docs/05's, and they stay
    here rather than in config because this is where the lexicons that select between them
    live — the number and the rule that picks it belong together.

    Never raises — an endpointing failure must not crash the turn; it falls back to the
    850ms default.
    """
    try:
        toks = tokens(text)
        if not toks:
            return default_secs
        tail = toks[-_TAIL_TOKENS:]
        for phrase in CONTINUATION_PHRASES:
            n = len(phrase)
            if n <= len(tail) and tail[-n:] == phrase:
                return continuation_secs
        last = tail[-1]
        if last in CONTINUATION_TOKENS:
            return continuation_secs
        for phrase in TERMINAL_PHRASES:
            n = len(phrase)
            if n <= len(tail) and tail[-n:] == phrase:
                return terminal_secs
        if last in TERMINAL_TOKENS or last in TERMINAL_PLACES:
            return terminal_secs
        if last.isdigit():
            return terminal_secs
        return default_secs
    except Exception:  # noqa: BLE001  # pragma: no cover - endpointing must never raise
        return default_secs


def vad_span_secs(start, stop) -> "float | None":
    """Actual speech duration between a VAD start/stop frame pair, in seconds.

    Both frames are emitted AFTER their confirmation window has already elapsed, so their
    timestamps sit `start_secs` / `stop_secs` past the acoustic events. The windows must
    be subtracted back out:

        span = (stop.timestamp - stop.stop_secs) - (start.timestamp - start.start_secs)

    The naive `stop.timestamp - start.timestamp` inflates every span by
    `stop_secs - start_secs` — 0.25s under the current config (start_secs 0.2, stop_secs
    0.45) and 0.55s before the 2026-08-04 retune. Either way it can put a short utterance
    above the 600ms threshold: the backchannel guard would then never fire and we would ship
    barge-in-on-everything, with no error and no failing test.

    The subtraction reads `stop_secs` off the FRAME, never a constant, which is what makes
    this survive a retune untouched — `test_the_backchannel_span_math_survives_the_retune`
    holds that property at both values. Hence the explicit test on this formula.

    Returns None when either frame is missing or malformed (offline tests run with
    `build_vad_fn=lambda: None`, so there are no VAD frames at all). Callers MUST treat
    None as "unknown, do not suppress" — never silently drop lead speech.
    """
    if start is None or stop is None:
        return None
    try:
        began = start.timestamp - start.start_secs
        ended = stop.timestamp - stop.stop_secs
    except (AttributeError, TypeError):
        return None
    span = ended - began
    return span if span >= 0 else 0.0


__all__ = [
    "BACKCHANNEL_MAX_SECS",
    "BARGE_IN_ATTEMPTS",
    "ENDPOINT_TERMINAL_SECS",
    "ENDPOINT_DEFAULT_SECS",
    "ENDPOINT_CONTINUATION_SECS",
    "BACKCHANNEL_TOKENS",
    "TERMINAL_TOKENS",
    "TERMINAL_PLACES",
    "CONTINUATION_TOKENS",
    "CONTINUATION_PHRASES",
    "is_backchannel",
    "endpoint_timeout_for",
    "vad_span_secs",
]
