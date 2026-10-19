"""The opt-in safety lab defaults to offline fakes and preserves speech identity."""

import asyncio
import importlib.util
from argparse import Namespace
from pathlib import Path

from roma.core.tts_settings import TTSSettings


def test_offline_safety_lab_exercises_all_fallbacks_and_shared_hindi_cache(monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "safety_lab_test", Path(__file__).resolve().parents[2] / "scripts/tts_safety_lab.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(
        module,
        "TTSSettings",
        lambda **kwargs: TTSSettings(_env_file=None, **kwargs),
    )
    result = asyncio.run(module.run(Namespace(provider="synthetic_pcm", audio_dir=None)))
    assert result["errors"] == 0 and len(result["cases"]) == 24
    assert result["cache"] == {"hits": 18, "misses": 6, "stores": 6, "coalesced": 0}
    assert result["synthetic_only"] and not result["network_calls"]
    assert not result["native_review_passed"]
    assert {row["spoken_language"] for row in result["cases"]} == {"hi"}
    assert {row["requested_language"] for row in result["cases"]} == {"gu", "hi", "en"}
    assert all(row["audit_ok"] for row in result["cases"])
    assert {event["category"] for event in result["audit_events"]} >= {
        "FEE",
        "SALARY",
        "PLACEMENT",
        "CERT",
        "DISCOUNT",
        "PRONUNCIATION",
    }
