"""Explicit, offline validation of sentence-tokenizer environment data."""

import os
from pathlib import Path

DEFAULT_NLTK_DATA = Path(__file__).resolve().parents[2] / "var/roma/nltk_data"
TOKENIZER_SETUP_MESSAGE = "TOKENIZER-DATA-MISSING: run make nltk-data before startup or tests."


class TokenizerSetupError(RuntimeError):
    pass


def require_nltk_data() -> None:
    import nltk.data
    from nltk.tokenize.punkt import load_punkt_params

    directory = Path(os.environ.get("NLTK_DATA") or DEFAULT_NLTK_DATA)
    # Register before opening: the installed NLTK validates reads against these roots.
    if str(directory) not in nltk.data.path:
        nltk.data.path.insert(0, str(directory))
    try:
        resource = nltk.data.find("tokenizers/punkt_tab/english/", paths=[str(directory)])
        load_punkt_params(resource)
    except (LookupError, OSError, ValueError):
        raise TokenizerSetupError(TOKENIZER_SETUP_MESSAGE) from None
