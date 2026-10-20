"""Slot extraction via the bounded LLM provider and validated JSON (docs/03).

Use model extraction rather than regex/dateparser for the idiomatic code-mix
values (`dhai baje`=2:30, `saanjhe`=evening, `kal`=tomorrow). This is the ONLY place a
second model call happens, and it runs ONLY in the slot-bearing stages (discover discovery,
pivot pivot, close close) — `turn.py` gates it, honoring "one turn = one LLM call unless a stage
provably needs one".

Two hard rules from docs/03 live here:
- The model does language understanding, NOT date arithmetic — it returns a relative
  `day_offset`/`weekday` + a wall-clock time; `timeresolve.py` turns that into an absolute
  Asia/Kolkata datetime IN CODE.
- `confidence < threshold` → the caller re-asks; a confidently-wrong slot burns the lead.

The `client` is passed in (the transport-neutral provider protocol) so tests inject a fake and no live API
is touched. Extraction never raises — on any error it returns a zero-confidence value so
the caller simply re-asks.
"""

import json
import logging
from contextlib import aclosing

from pydantic import BaseModel, Field

from roma.core.ai_contracts import LLMMessage, LLMRequest
from roma.domain.safety.inputs import DISCOVERY_FIELDS, caller_text, discovery_value

_log = logging.getLogger("roma.domain.conversation")

SLOT_MODEL = "claude-haiku-5-5"


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
        description="true only if the lead AGREED to a slot (pivot), not merely mentioned a time",
    )
    readback_confirmed: bool = Field(
        default=False, description="true only if the lead CONFIRMED Roma's readback (close)"
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
    from roma.domain.safety.inputs import slot_text

    valid = [s for s in offered[:2] if slot_text(s) is not None]
    lines = "\n".join(f"{i}. {s}" for i, s in enumerate(valid, start=1))
    return f"OFFERED SLOTS (Roma proposed these):\n{lines}\n\nLead's reply: "


_DISCOVERY_JSON_HINT = '\nReturn ONLY JSON: {"value": string or null, "confidence": 0.0-1.0}'

_DISCOVERY_MAX_TOKENS = 40


async def _extract_json(client, system, text, model, max_tokens):
    request = LLMRequest(
        messages=(LLMMessage("system", system), LLMMessage("user", text)),
        max_tokens=min(max_tokens, getattr(client, "max_output_tokens", max_tokens)),
        metadata={"purpose": "extraction"},
    )
    parts = []
    finished = False
    size = 0
    async with aclosing(client.generate(request)) as stream:
        async for chunk in stream:
            if chunk.metrics is not None and chunk.metrics.error_code is not None:
                raise ValueError("extraction provider failed")
            size += len(chunk.text.encode("utf-8"))
            if size > 8192:
                raise ValueError("extraction output exceeds byte budget")
            parts.append(chunk.text)
            if chunk.finish_reason is not None:
                if chunk.finish_reason not in {"stop", "end_turn"}:
                    raise ValueError("incomplete extraction")
                finished = True
    if not finished:
        raise ValueError("extraction ended without completion")
    return model.model_validate_json("".join(parts))


async def extract_discovery_slot(client, text: str, slot_name: str) -> DiscoveryValue:
    """Extract one bounded profile field; malformed/incomplete output means re-ask."""
    try:
        text = caller_text(text)
        if slot_name not in DISCOVERY_FIELDS:
            return DiscoveryValue()
        parsed = await _extract_json(
            client,
            _DISCOVERY_SYS + _DISCOVERY_JSON_HINT,
            f"Field: {slot_name}\nReply: {text}",
            DiscoveryValue,
            _DISCOVERY_MAX_TOKENS,
        )
        valid = discovery_value(slot_name, parsed.value)
        if parsed.value is not None and valid is None:
            return DiscoveryValue()
        parsed.value = valid
        return parsed
    except Exception:  # noqa: BLE001 -- must not crash the turn or expose caller text.
        _log.warning("discovery slot extraction failed for %s; re-asking", slot_name)
        return DiscoveryValue()


async def extract_time_slot(client, text: str, *, offered=None) -> TimeSlot:
    """Validate every extracted booking field; date arithmetic remains in code.

    The schema is supplied in the fixed system message and validated locally. Only
    completed JSON is accepted; truncation, provider failures or invalid fields re-ask.
    """
    try:
        text = caller_text(text)
        return await _extract_json(
            client,
            _TIME_SYS + "\nReturn ONLY JSON matching this schema: "
            + json.dumps(TimeSlot.model_json_schema(), ensure_ascii=False),
            _offer_block(offered) + text,
            TimeSlot,
            256,
        )
    except Exception:  # noqa: BLE001 -- must not crash the turn or expose caller text.
        _log.warning("time slot extraction failed; re-asking")
        return TimeSlot()


__all__ = [
    "DiscoveryValue",
    "TimeSlot",
    "extract_discovery_slot",
    "extract_time_slot",
    "SLOT_MODEL",
]
