import json
import subprocess
import sys
from pathlib import Path

import pytest
from roma.eval.audio import report


@pytest.mark.parametrize("delay", [40, 50, 60])
def test_fixed_buffer_impairment_fixtures(delay):
    result = report(delay_ms=delay)
    cases = result["cases"]
    assert cases["clean"]["output_frames"] == 50
    assert cases["loss"]["sequence_gaps"] == 2
    assert cases["loss"]["output_frames"] == 48
    assert cases["reordering"]["reordered"] > 0
    assert cases["reordering"]["sequence_gaps"] == 0
    assert cases["duplication"]["duplicates"] == 1
    assert cases["burst_delay"]["sequence_gaps"] > 0
    assert all(
        case["ordered"] and case["aligned_20ms_pcm"] and case["pending"] == 0
        for case in cases.values()
    )
    assert not result["adaptive_or_plc"] and not result["asr_or_native_quality_measured"]


def test_audio_lab_command_needs_no_env_or_services(monkeypatch, tmp_path):
    root = Path(__file__).resolve().parents[2]
    for key in ("OPENAI_API_KEY", "SARVAM_API_KEY", "DATABASE_URL", "REDIS_URL"):
        monkeypatch.delenv(key, raising=False)
    result = subprocess.run(
        [sys.executable, str(root / "scripts/audio_lab.py")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    parsed = json.loads(result.stdout)
    assert parsed["fixed_jitter_ms"] == 60 and parsed["cases"]["clean"]["sequence_gaps"] == 0
    assert not list(tmp_path.iterdir())


def test_reference_metrics_are_reproducible():
    result = report()
    print(
        json.dumps(
            {
                name: {
                    key: value
                    for key, value in case.items()
                    if key
                    in (
                        "output_frames",
                        "sequence_gaps",
                        "reordered",
                        "duplicates",
                        "late",
                        "max_wait_ms",
                    )
                }
                for name, case in result["cases"].items()
            },
            sort_keys=True,
        )
    )
    assert result["cases"]["clean"]["max_wait_ms"] == 60
