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


def test_generation_uses_the_approved_claude_model():
    assert LLM_MODEL == "claude-haiku-5-5"


def test_slot_extraction_uses_the_same_approved_provider_model():
    assert SLOT_MODEL == LLM_MODEL
