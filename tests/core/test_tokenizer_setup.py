import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_missing_tokenizer_fails_fast_before_sentence_tests(tmp_path):
    env = {**os.environ, "NLTK_DATA": str(tmp_path / "missing"), "PYTHONDONTWRITEBYTECODE": "1"}
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "tests/realtime/test_sentences.py"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode != 0
    assert "TOKENIZER-DATA-MISSING" in result.stdout + result.stderr
    assert "make nltk-data" in result.stdout + result.stderr
    assert "No such file or directory" not in result.stdout + result.stderr


def test_app_refuses_missing_tokenizer_before_constructing_services(tmp_path, monkeypatch):
    import pytest
    from roma.main import create_app

    monkeypatch.setenv("NLTK_DATA", str(tmp_path / "missing"))
    with pytest.raises(RuntimeError, match="TOKENIZER-DATA-MISSING"):
        create_app()


def test_voice_preflight_refuses_missing_tokenizer(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import pytest
    from scripts.voice_prototype import local_preflight

    monkeypatch.setenv("NLTK_DATA", str(tmp_path / "missing"))
    with pytest.raises(RuntimeError, match="TOKENIZER-DATA-MISSING"):
        local_preflight(SimpleNamespace())


def test_direct_startup_registers_repo_default_without_make_environment(monkeypatch):
    import nltk.data
    from roma.core.tokenizer import require_nltk_data

    monkeypatch.delenv("NLTK_DATA", raising=False)
    monkeypatch.setattr(nltk.data, "path", [])
    require_nltk_data()
