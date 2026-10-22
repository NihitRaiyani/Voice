from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("level", ["04", "06"])
def test_level_brief_starts_with_current_readiness_without_claiming_a_gate(level):
    text = (ROOT / f"docs/levels/level-{level}.md").read_text()
    assert "## Current readiness" in "\n".join(text.splitlines()[:5])
    readiness = text.split("## Current readiness", 1)[1].split("\n## ", 1)[0]
    assert "Whisper Turbo 4-bit MLX" in readiness
    assert "weights were removed" in readiness
    assert "No gate is marked passed" in readiness


def test_indic_cache_availability_checklist_is_explicitly_historical():
    text = (ROOT / "docs/levels/level-06.md").read_text()
    lines = [line for line in text.splitlines() if "Pinned IndicConformer runtime/cache" in line]
    assert lines and all("Historical" in line for line in lines)


def test_speech_contract_marks_previous_indic_routing_as_historical():
    text = (ROOT / "docs/05-endpointing-vad.md").read_text()
    assert "**Historical pre-Turbo routing:**" in text
    assert "Current local development ASR is Whisper Turbo 4-bit MLX" in text
