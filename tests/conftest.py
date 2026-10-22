"""Fail once, before tests run, when explicit offline setup is incomplete."""

import pytest
from roma.core.tokenizer import require_nltk_data


@pytest.fixture(scope="session", autouse=True)
def tokenizer_ready():
    try:
        require_nltk_data()
    except RuntimeError as exc:
        pytest.exit(str(exc), returncode=4)
