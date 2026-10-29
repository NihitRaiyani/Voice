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


# Separate experimental native-script hints: nouns/time words plus negation,
# never a selected clock time or an affirmation/confirmation answer.
NATIVE_PROMPTS = {
    "hi": "वेल्टेक, वडोदरा, मुलाकात, कल, दोपहर, शाम, बजे, नहीं।",
    "gu": "વેલ્ટેક, વડોદરા, મુલાકાત, કાલે, બપોરે, સાંજે, વાગ્યે, નહીં.",
}
NATIVE_VERSION = "weltec-native-booking-vocabulary-v1"
NATIVE_SHA256 = {
    "hi": "49443dd5ae01683ba6b9132ea04f2a66a2d45f6910c98c9aac27181833152766",
    "gu": "c386754ee4203b19ee7548b996cd4a2e8663b441c231f88592ae81d12811de26",
}
NATIVE_TOKEN_COUNTS = {"hi": 58, "gu": 147}
NATIVE_MAX_UTF8_BYTES = 192
NATIVE_MAX_TOKENS = 160
# 448 context - 160 prompt - sot_prev - 4 SOT/task/language/no-timestamps - 1 spare.
NATIVE_SAMPLE_LEN = 282


def native_prompt_details(enabled: bool, language: str | None) -> dict[str, str | int | bool]:
    if type(enabled) is not bool:
        raise ValueError("Whisper native booking vocabulary requires a boolean opt-in")
    if not enabled or language is None or language not in NATIVE_PROMPTS:
        return {"enabled": False, "sample_len": 440}
    prompt = NATIVE_PROMPTS[language]
    raw = prompt.encode("utf-8")
    if (len(raw) > NATIVE_MAX_UTF8_BYTES
            or hashlib.sha256(raw).hexdigest() != NATIVE_SHA256[language]
            or not 0 < NATIVE_TOKEN_COUNTS[language] <= NATIVE_MAX_TOKENS):
        raise ValueError("Approved native Whisper vocabulary identity differs")
    return {
        "enabled": True, "version": NATIVE_VERSION, "language": language,
        "sha256": NATIVE_SHA256[language], "utf8_bytes": len(raw),
        "tokens": NATIVE_TOKEN_COUNTS[language], "max_utf8_bytes": NATIVE_MAX_UTF8_BYTES,
        "max_tokens": NATIVE_MAX_TOKENS, "sample_len": NATIVE_SAMPLE_LEN,
    }


def load_native_prompt(enabled: bool, language: str | None) -> str | None:
    details = native_prompt_details(enabled, language)
    if not details["enabled"] or language is None:
        return None
    from mlx_whisper.tokenizer import get_tokenizer

    prompt = NATIVE_PROMPTS[language]
    tokenizer = get_tokenizer(True, num_languages=100, language=language, task="transcribe")
    if len(tokenizer.encode(" " + prompt.strip())) != NATIVE_TOKEN_COUNTS[language]:
        raise ValueError("Approved native Whisper vocabulary token identity differs")
    return prompt


def decoder_vocabulary(legacy: bool, native: bool, language: str | None) -> tuple[str | None, int]:
    # Native hints take precedence only on an existing Hindi/Gujarati route.
    # English and unknown routes retain the independently selected legacy policy.
    sample_len = sample_length(legacy)
    prompt = load_native_prompt(native, language)
    if prompt is not None:
        return prompt, NATIVE_SAMPLE_LEN
    return load_prompt(legacy), sample_len
