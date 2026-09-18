"""The LLM model config, pinned — and pinned to a price.

TTS already has this pin (`test_media_stt.py`: `assert s.model == "bulbul:v3"`), and it
exists because `bulbul:v2` regressed onto a live call and garbled every code-mix line. The
LLM had no equivalent, which left the one config with a 16.7x cost multiplier attached to
it as the one config nothing guarded.
"""

from roma.domain.appointments.slots import SLOT_MODEL
from roma.domain.costs.spend import PRICES
from roma.realtime.pipeline import LLM_MODEL


def test_every_model_we_send_tokens_to_has_a_price():
    """The structural fix for the gpt-4o switch, and the one assertion here that matters
    more than the exact model names below.

    `roma.eval.cost` used to hold ONE hardcoded price table — gpt-4o-mini's — with nothing
    tying it to the model actually configured. Swapping `LLM_MODEL` to gpt-4o would have
    left the meter billing 16.7x too little and the ₹100 cap reporting ₹6 when the money
    was gone. Requiring every configured model to be a key of `PRICES` makes that class of
    mistake fail the suite instead of the budget.
    """
    for name, model in (("LLM_MODEL", LLM_MODEL), ("SLOT_MODEL", SLOT_MODEL)):
        assert model in PRICES, (
            f"{name}={model!r} has no entry in roma.domain.costs.spend.PRICES. Price it before you "
            f"ship it — an unpriced model bills at a guess."
        )


def test_generation_runs_on_gpt_4o():
    """The conversation model, moved up from gpt-4o-mini (2026-07-27).

    mini kept losing the prompt's hard rules under pressure — most visibly hard rule 10
    (p7_close: "do not sign off until SLOT STATUS says the visit is locked"), where it
    signed off with "milte hain... confirmation WhatsApp par bhej rahi hoon" on a call
    whose slot was merely `accepted`, never locked. That is the exact failure the whole
    seven-phase machine exists to prevent, and no further prompt wording moved it.
    """
    assert LLM_MODEL == "gpt-4o"


def test_slot_extraction_stays_on_the_cheap_model():
    """Deliberately NOT moved to gpt-4o, and the reason is worth keeping.

    The extractor is a small structured-output call that runs on the turn's critical path
    (`turn.py` awaits it before the reply can start), so its latency is added to every
    single response — against a five-minute call budget where OpenAI TTFB was already
    measured at 0.919s p50.

    Its one known weakness — under-confidence on a plainly-spoken time, `confidence=0.50`
    on "4 બજે" — is already corrected structurally by `_corroborate()` in `turn.py`, which
    floors confidence when the transcript independently carries time evidence. Paying 16.7x
    and adding latency to fix a problem that is already fixed is not a trade worth making.
    """
    assert SLOT_MODEL == "gpt-4o-mini"
