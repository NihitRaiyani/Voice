import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest
from roma.eval.speech import report


def test_report_proves_short_ack_pause_smoothing_and_restore():
    result = asyncio.run(report())
    assert result["asr_calls"] == 4
    assert result["restore_matches"] is True
    cases = result["cases"]
    assert len(cases) == 4 and cases[0]["reason"] == "silence"
    assert cases[0]["duration_ms"] >= 64
    assert cases[1]["duration_ms"] > 500
    assert cases[2]["route"] != "ENGLISH" and cases[3]["route"] == "ENGLISH"
    assert all("asr_ms" not in row for row in cases)
    assert "SEO" in cases[0]["text"] and "Google Ads" in cases[1]["text"]


def test_cli_defaults_to_synthetic_without_reading_real_env(tmp_path):
    script = Path(__file__).resolve().parents[2] / "scripts" / "speech_lab.py"
    (tmp_path / ".env").write_text("STT_PROVIDER=hf_indic_conformer\nHF_TOKEN=do-not-read\n")
    result = subprocess.run(
        [sys.executable, str(script)], cwd=tmp_path, capture_output=True, text=True, check=True
    )
    assert json.loads(result.stdout)["synthetic_only"]
    assert "do-not-read" not in result.stdout + result.stderr


@pytest.mark.parametrize("payload", [b"", b"not-a-WAV"])
def test_malformed_wav_cli_has_safe_error_without_traceback(tmp_path, payload):
    script = Path(__file__).resolve().parents[2] / "scripts" / "speech_lab.py"
    wav = tmp_path / "private-caller.wav"
    wav.write_bytes(payload)
    result = subprocess.run(
        [sys.executable, str(script), "--provider", "whisper", "--wav", str(wav)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "Speech lab failed" in result.stderr
    assert "Traceback" not in result.stderr
    assert "private-caller" not in result.stderr
