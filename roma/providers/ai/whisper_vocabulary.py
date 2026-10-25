"""Opt-in, fixed reviewed nouns only; never caller text or booking authority.

Sources: safety/inputs.py BRANCHES, conversation/state.py active course,
prompts/stages/value.md and knowledge/routing.py reviewed module names.
"""

import hashlib

PROMPT = (
    "Weltec Institute, Vadodara, Ahmedabad, Digital Marketing, SEO, "
    "social media marketing, Google Ads."
)
VERSION = "weltec-booking-vocabulary-v1"
ROUTER_VERSION = "whisper-turbo-vocabulary-v1"
PROMPT_SHA256 = "ab1cd45ce268d544265b04ce208d822a4b61abbdc6a24cd0691ee17ab5416434"
PROMPT_TOKENS = 24
MAX_UTF8_BYTES = 128
MAX_TOKENS = 32
# 448 context - 32 prompt - sot_prev - 4 SOT/task/language/no-timestamps - 1 spare.
PROMPT_SAMPLE_LEN = 410


def sample_length(enabled: bool) -> int:
    if type(enabled) is not bool:
        raise ValueError("Whisper booking vocabulary requires a boolean opt-in")
    return PROMPT_SAMPLE_LEN if enabled else 440


def prompt_details(enabled: bool) -> dict[str, str | int | bool]:
    """Metadata only; no SDK import, prompt content, caller input or model load."""
    sample_len = sample_length(enabled)
    if not enabled:
        return {"enabled": False, "sample_len": sample_len}
    raw = PROMPT.encode("utf-8")
    if (len(raw) > MAX_UTF8_BYTES or hashlib.sha256(raw).hexdigest() != PROMPT_SHA256
            or not 0 < PROMPT_TOKENS <= MAX_TOKENS):
        raise ValueError("Approved Whisper vocabulary identity differs")
    return {
        "enabled": True, "version": VERSION, "sha256": PROMPT_SHA256,
        "utf8_bytes": len(raw), "tokens": PROMPT_TOKENS,
        "max_utf8_bytes": MAX_UTF8_BYTES, "max_tokens": MAX_TOKENS,
        "sample_len": sample_len,
    }


def load_prompt(enabled: bool) -> str | None:
    """Check the pinned SDK's BPE count lazily; this never loads model weights."""
    prompt_details(enabled)
    if not enabled:
        return None
    from mlx_whisper.tokenizer import get_tokenizer

    tokenizer = get_tokenizer(True, num_languages=100, language="en", task="transcribe")
    # Both SDK entry points prepend exactly one space before encoding a string.
    if len(tokenizer.encode(" " + PROMPT.strip())) != PROMPT_TOKENS:
        raise ValueError("Approved Whisper vocabulary token identity differs")
    return PROMPT
