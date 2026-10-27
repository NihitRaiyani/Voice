#!/usr/bin/env python3
"""Validate the explicit non-secret deployment target; never loads working .env."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {
    "ROMA_TARGET_PROFILE_VERSION": "1",
    "APP_ENV": "production",
    "LLM_PROVIDER": "claude",
    "ANTHROPIC_MODEL": "claude-haiku-5-5",
    "ANTHROPIC_EFFORT": "low",
    "VOICE_SPEECH_MODE": "local_http",
    "STT_PROVIDER": "whisper_mlx",
    "WHISPER_MLX_MODEL_DIR": "var/roma/models/whisper-large-v3-turbo-mlx-4bit",
    "TTS_PROVIDER": "indic_tts",
    "INDIC_TTS_MANIFEST": "var/roma/models/indic-tts/manifest.json",
    "TTS_VOICE": "female",
    "EMBEDDING_PROVIDER": "local_multilingual",
    "EMBEDDING_MANIFEST": "var/roma/models/multilingual-e5-small/manifest.json",
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "AUTH_ALLOW_LEGACY_API_TOKEN": "false",
    "RAG_ENABLED": "false",
    "SPEECH_SERVICE_URL": "http://127.0.0.1:8091",
}


def read_profile(path: Path) -> dict[str, str]:
    if path.stat().st_size > 16384:
        raise ValueError("Target profile exceeds size bound")
    values = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or key in values:
            raise ValueError("Target profile requires unique literal fields")
        if not value or any(marker in value for marker in ("$", "`", "\x00")):
            raise ValueError("Target profile requires literal nonempty values")
        values[key] = value
    return values


def validate_profile(values: dict[str, str]) -> list[str]:
    # The finite schema excludes credentials and prevents silently accepted knobs.
    errors = [f"target field rejected: {key}" for key in sorted(EXPECTED) if values.get(key) != EXPECTED[key]]
    if set(values) - set(EXPECTED):
        errors.append("target contains unknown fields")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, default=ROOT / "configs/production.local.env")
    args = parser.parse_args()
    try:
        errors = validate_profile(read_profile(args.profile))
    except (ValueError, OSError):
        errors = ["target profile unreadable or invalid"]
    print(json.dumps({"passed": not errors, "errors": errors, "scope": "explicit target configuration only; runtime secrets/model hashes/native acceptance remain separate"}))
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
