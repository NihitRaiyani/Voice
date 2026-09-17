"""Slot extraction via OpenAI structured output (docs/03).

docs/03: "Use OpenAI structured output, NOT regex/dateparser" for the idiomatic code-mix
values (`dhai baje`=2:30, `saanjhe`=evening, `kal`=tomorrow). This is the ONLY place a
second model call happens, and it runs ONLY in the slot-bearing phases (P2 discovery,
P5 pivot, P7 close) — `turn.py` gates it, honoring "one turn = one LLM call unless a phase
provably needs one".

Two hard rules from docs/03 live here:
- The model does language understanding, NOT date arithmetic — it returns a relative
  `day_offset`/`weekday` + a wall-clock time; `timeresolve.py` turns that into an absolute
  Asia/Kolkata datetime IN CODE.
- `confidence < threshold` → the caller re-asks; a confidently-wrong slot burns the lead.

The `client` is passed in (duck-typed AsyncOpenAI) so tests inject a fake and no live API
is touched. Extraction never raises — on any error it returns a zero-confidence value so
the caller simply re-asks.
"""

import logging

from pydantic import BaseModel, Field

_log = logging.getLogger("roma.controller")

SLOT_MODEL = "gpt-4o-mini"


class DiscoveryValue(BaseModel):
    """A single discovery-slot value the lead just gave (education, city, ...)."""

    value: "str | None" = Field(
        default=None, description="the extracted value, or null if the lead did not answer"
    )
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    raw: str = Field(default="", description="the lead's own words for this value")


class TimeSlot(BaseModel):
    """A visit time parsed from the lead's utterance. The model fills the language it heard;
    `timeresolve.resolve_time_slot` converts it to an absolute datetime in code."""

    day_offset: "int | None" = Field(
        default=None,
        description="days from today: aaj=0, kal=1, parso=2; null if a weekday was named",
    )
    weekday: "str | None" = Field(
        default=None,
        description="lowercase english weekday if named (monday..sunday), else null",
    )
    hour: "int | None" = Field(
        default=None, description="clock hour the lead said (1-12 or 0-23)"
    )
    minute: int = Field(default=0, ge=0, le=59)
    period: "str | None" = Field(
        default=None,
        description="one of morning/afternoon/evening/night from cues like subah/saanjhe/raat",
    )
    accepted: bool = Field(
        default=False,
        description="true only if the lead AGREED to a slot (P5), not merely mentioned a time",
    )
    readback_confirmed: bool = Field(
        default=False, description="true only if the lead CONFIRMED Roma's readback (P7)"
    )
    chose_offer: "int | None" = Field(
        default=None,
        description=(
            "1 or 2 if the lead picked one of the OFFERED slots listed in the user message "
            "— by ordinal ('pehla', 'doosra', 'biju'), by day alone ('Tuesday', 'મંગળવારે'), "
            "by time alone ('gyaarah baje'), or anaphorically ('wahi', 'તે જ', 'that one'). "
            "null if they named a time that is not one of the offers, or named none."
        ),
    )
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    raw: str = Field(default="", description="the lead's own words for the time")


_DISCOVERY_SYS = (
    "You extract ONE profile field from a prospective student's short code-mix "
    "(Hindi/Gujarati/English) reply on a phone call. Return only what they actually said. "
    "If they did not answer the asked field, set value to null and confidence low. "
    "For the field `lead_name`: return ONLY the name, not the sentence around it — "
    "'mera naam Nikhil hai' -> 'Nikhil', 'જી, હું પ્રિયા' -> 'Priya'. A polite filler with no "
    "name in it ('ji', 'haan ji', 'bolo') is NOT a name: return null. If they decline or "
    "deflect ('wo baad mein', 'kyun chahiye'), return null — never invent one."
)

_TIME_SYS = (
    "You parse a visit time from a prospective student's short code-mix reply "
    "(Gujarati/Hindi/English). "
    "Hindi: dhai=2:30, sawa=+15, paune=-15, saadhe=+30; subah=morning, dopahar=afternoon, "
    "shaam/saanjhe=evening, raat=night; aaj=0, kal=1, parso=2; baje=o'clock. "
    "Gujarati: savare=morning, bapore=afternoon, saanje=evening, raatre=night; "
    "aaje=0, kaale=1, parmadivase=2; vagye=o'clock; sada=+30, sava=+15, pona=-15, "
    "dodh=1:30, adhi=2:30. "
    "Gujarati weekdays somvaar/mangalvaar/budhvaar/guruvaar/shukravaar/shanivaar/ravivaar "
    "and Hindi somvaar..ravivaar must be returned in the `weekday` field as LOWERCASE "
    "ENGLISH (monday..sunday) — never in the original script. The ENGLISH weekday names "
    "also arrive transliterated into Gujarati or Devanagari script — વેનસડે=wednesday, "
    "મંડે=monday, ટ્યુઝડે=tuesday, સનડે=sunday, सोमवार=monday — map those to English too. "
    "'weekend' / 'વીકેન્ડ' / 'shanivaar-ravivaar' means the coming SATURDAY — return "
    "weekday=saturday. 'weekday' / 'working day' names Monday-to-Friday as a class, not a "
    "particular day: leave weekday and day_offset null for it. "
    "Return the RELATIVE day and the wall-clock time only — do NOT compute a calendar date. "
    "NEVER GUESS A DAY THE LEAD DID NOT SAY. If their reply names only a time ('teen baje', "
    "'12 baje subah'), leave BOTH day_offset and weekday null — do not copy a day out of the "
    "OFFERED SLOTS list to fill the gap. Null is the correct answer and the caller knows "
    "which day is under discussion; a guessed day books the wrong one. "
    "Set accepted true if the lead agreed to a slot OR stated an intention to come at a "
    "named day or time: 'Tuesday aavish', 'kal aa jaunga', 'shaam ko aa jata hoon' are all "
    "acceptances. A QUESTION about a time ('Tuesday ko kitne baje?') is not, and neither is "
    "a REFUSAL — 'nahi', 'ना', 'નહીં', 'koi dusra na do' mean accepted false and chose_offer "
    "null, however emphatic the rest of the sentence sounds. "
    "Set readback_confirmed true only if they confirmed a read-back. "
    "If the user message lists OFFERED SLOTS and the lead picked one of them — by ordinal, "
    "by day alone, by time alone, or anaphorically — set chose_offer to 1 or 2 accordingly. "
    "CONFIDENCE IS ABOUT THE AUDIO, NOT ABOUT HOW MUCH THE LEAD SAID. If they clearly "
    "named a clock time or a day, set confidence 0.9 or above even when that is ALL they "
    "said — a bare '4 baje' is completely clear and must not be hedged. Reserve low "
    "confidence for a reply where you genuinely cannot tell what was spoken. "
    "If no time is present, set confidence low."
)


def _offer_block(offered) -> str:
    """The offered slots, as text for the user message. Empty string when there are none.

    Goes in the USER message, never in `_TIME_SYS`: the offers change every call, and a
    system prompt that varies per turn defeats prefix caching for no benefit.
    """
    if not offered:
        return ""
    lines = "\n".join(f"{i}. {s}" for i, s in enumerate(offered, start=1))
    return f"OFFERED SLOTS (Roma proposed these):\n{lines}\n\nLead's reply: "


_DISCOVERY_JSON_HINT = '\nReturn ONLY JSON: {"value": string or null, "confidence": 0.0-1.0}'

_DISCOVERY_MAX_TOKENS = 40


async def extract_discovery_slot(client, text: str, slot_name: str) -> DiscoveryValue:
    """Extract the value of `slot_name` (education/passing_year/current_status/city/
    timing_constraint) from the lead's reply. Never raises.

    ## Why this is `json_object` and not `beta...parse`

    This call is ON THE CRITICAL PATH: `turn.py` awaits it before the reply prompt is
    assembled, so every millisecond here is dead air the lead hears (docs/05). Measured
    2026-08-01, same model, same prompt, four representative replies:

        beta.parse           median 1.61s   spread 0.85-2.46s
        json_object + cap    median 0.87s   spread 0.83-0.97s

    Half the latency, and — the part that matters more for how a call FEELS — the 2.46s
    tail disappears. Erratic turn timing reads as a broken line; consistent timing reads as
    a person thinking.

    `DiscoveryValue` is two scalars, so strict schema enforcement buys little: a malformed
    reply fails `model_validate_json` and returns the same zero-confidence value the
    exception path already returns, which `turn.py` treats as "re-ask". `extract_time_slot`
    deliberately KEEPS `beta.parse` — its schema is six coupled fields on the booking path,
    where a silently mis-parsed field is a wrong visit, not a re-ask.
    """
    try:
        completion = await client.chat.completions.create(
            model=SLOT_MODEL,
            messages=[
                {"role": "system", "content": _DISCOVERY_SYS + _DISCOVERY_JSON_HINT},
                {"role": "user", "content": f"Field: {slot_name}\nReply: {text}"},
            ],
            response_format={"type": "json_object"},
            max_tokens=_DISCOVERY_MAX_TOKENS,
        )
        content = completion.choices[0].message.content
        return DiscoveryValue.model_validate_json(content) if content else DiscoveryValue()
    except Exception:  # noqa: BLE001  # pragma: no cover - must not crash the turn
        _log.warning("discovery slot extraction failed for %s; re-asking", slot_name)
        return DiscoveryValue()


async def extract_time_slot(client, text: str, *, offered=None) -> TimeSlot:
    """Extract a visit time (+ accepted / readback_confirmed) from the lead's reply. The
    absolute date is resolved later in code (timeresolve). Never raises.

    `offered` is the spoken form of the slots Roma proposed, so the model can resolve a
    reply that only makes sense relative to them — "Tuesday", "pehla wala", "wahi". Without
    it those are unparseable by construction, which is exactly how a live acceptance was
    lost. Keyword-only with a default, so every existing caller is unaffected.
    """
    try:
        completion = await client.beta.chat.completions.parse(
            model=SLOT_MODEL,
            messages=[
                {"role": "system", "content": _TIME_SYS},
                {"role": "user", "content": _offer_block(offered) + text},
            ],
            response_format=TimeSlot,
        )
        parsed = completion.choices[0].message.parsed
        return parsed if parsed is not None else TimeSlot()
    except Exception:  # noqa: BLE001  # pragma: no cover - must not crash the turn
        _log.warning("time slot extraction failed; re-asking")
        return TimeSlot()


__all__ = [
    "DiscoveryValue",
    "TimeSlot",
    "extract_discovery_slot",
    "extract_time_slot",
    "SLOT_MODEL",
]
