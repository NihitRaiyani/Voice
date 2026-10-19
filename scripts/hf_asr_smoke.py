"""Explicit public FLEURS smoke; no model downloads, private audio or paid router calls."""

import argparse
import asyncio
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import wave
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402
from roma.core.speech_settings import SpeechSettings  # noqa: E402
from roma.eval.hosted_asr import score  # noqa: E402
from roma.providers.ai.contracts import (  # noqa: E402
    AudioFormat,
    ProviderUnavailable,
    STTRequest,
)
from roma.providers.ai.registry import build_stt_provider  # noqa: E402

LANGUAGES = {"gu": "gu_in", "hi": "hi_in", "en": "en_us"}


async def bounded_get(client, url, *, limit, params=None):
    async with client.stream("GET", url, params=params) as response:
        response.raise_for_status()
        body = bytearray()
        async for chunk in response.aiter_bytes(chunk_size=16384):
            body.extend(chunk)
            if len(body) > limit:
                raise ValueError("Public benchmark response exceeds its bound")
        return bytes(body)


def read_wav(path):
    with wave.open(str(path), "rb") as source:
        if (
            source.getnchannels(),
            source.getsampwidth(),
            source.getframerate(),
            source.getcomptype(),
        ) != (1, 2, 16000, "NONE"):
            raise ValueError("Public benchmark must be mono 16 kHz PCM16 WAV")
        frames = source.getnframes()
        if not 0 < frames <= 480000:
            raise ValueError("Public sample must contain at most thirty seconds")
        pcm = source.readframes(frames + 1)
        if len(pcm) != frames * 2:
            raise ValueError("Public benchmark audio is truncated or unaligned")
        return pcm


def decode_flac(payload, destination):
    # Decode audio only, using an existing native tool. No model/runtime installation.
    with tempfile.TemporaryDirectory() as folder:
        source, target = Path(folder) / "public.flac", Path(folder) / "public.wav"
        source.write_bytes(payload)
        if shutil.which("afconvert"):
            command = ["afconvert", "-f", "WAVE", "-d", "LEI16", str(source), str(target)]
        elif shutil.which("ffmpeg"):
            command = [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                str(source),
                "-ac",
                "1",
                "-ar",
                "16000",
                "-t",
                "30.001",
                "-c:a",
                "pcm_s16le",
                str(target),
            ]
        else:
            raise ValueError(
                "Install an audio decoder (afconvert/ffmpeg), or provision the public WAV cache separately"
            )
        subprocess.run(command, check=True, capture_output=True, timeout=20)
        read_wav(target)
        destination.write_bytes(target.read_bytes())


async def fetch_samples(folder):
    folder.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=30, trust_env=False, follow_redirects=False) as client:
        for language, config in LANGUAGES.items():
            raw = await bounded_get(
                client,
                "https://datasets-server.huggingface.co/rows",
                limit=1024 * 1024,
                params={
                    "dataset": "google/fleurs",
                    "config": config,
                    "split": "validation",
                    "offset": 0,
                    "length": 1,
                },
            )
            row = json.loads(raw)["rows"][0]["row"]
            audio_url = row["audio"][0]["src"]
            parsed = urlsplit(audio_url)
            if (
                parsed.scheme != "https"
                or parsed.hostname != "datasets-server.huggingface.co"
                or not parsed.path.startswith("/cached-assets/google/fleurs/")
                or parsed.username
                or parsed.password
            ):
                raise ValueError("Unexpected public benchmark audio origin")
            payload = await bounded_get(client, audio_url, limit=2 * 1024 * 1024)
            wav = folder / (language + ".wav")
            decode_flac(payload, wav)
            metadata = {
                "dataset": "google/fleurs",
                "config": config,
                "split": "validation",
                "row": 0,
                "reference": row["transcription"],
                "license": "CC-BY-4.0",
                "sha256": hashlib.sha256(wav.read_bytes()).hexdigest(),
            }
            (folder / (language + ".json")).write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2)
            )


async def run(args):
    if not args.allow_network:
        raise ValueError("Hosted ASR smoke requires explicit --allow-network")
    if args.fetch_public_samples:
        await fetch_samples(args.audio_dir)
    config = SpeechSettings(stt_provider="hf_public_lab")
    provider = build_stt_provider(config)
    cases = []
    for language in LANGUAGES:
        path = args.audio_dir / (language + ".wav")
        metadata = json.loads((args.audio_dir / (language + ".json")).read_text())
        if (
            metadata.get("dataset") != "google/fleurs"
            or metadata.get("config") != LANGUAGES[language]
            or metadata.get("split") != "validation"
            or metadata.get("row") != 0
            or hashlib.sha256(path.read_bytes()).hexdigest() != metadata.get("sha256")
        ):
            raise ValueError("Public benchmark cache identity does not match")
        pcm = read_wav(path)
        started = asyncio.get_running_loop().time()
        row = {**metadata, "language": language, "audio_seconds": len(pcm) / 32000}
        try:
            result = await provider.transcribe(
                STTRequest(
                    pcm,
                    AudioFormat(16000),
                    language,
                    {"data_classification": "public_benchmark"},
                )
            )
            row.update(
                {
                    "hypothesis": result.text,
                    **score(metadata["reference"], result.text),
                    "error": None,
                }
            )
        except ProviderUnavailable:
            row["error"] = "hosted-asr-unavailable"
        row["request_ms"] = round((asyncio.get_running_loop().time() - started) * 1000, 2)
        cases.append(row)
    report = {
        "schema": "roma-hosted-asr-smoke-v1",
        "recorded_at": datetime.now(UTC).isoformat(),
        "provider": "hf_public_lab",
        "indic_endpoint": config.hf_indic_space_url,
        "whisper_endpoint": config.hf_whisper_space_url,
        "whisper_api_name": config.hf_whisper_api_name,
        "cases": cases,
        "public_samples_only": True,
        "new_model_weights_downloaded": False,
        "paid_router_used": False,
        "native_review_passed": False,
        "production_voice_approved": False,
        "limits": "Three clean read-speech samples; not representative WER/CER, code-mix/noise, p95 or voice approval. Request time includes network/upload/queue/inference, not model compute RTF.",
    }
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if any(row["error"] for row in cases) else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--fetch-public-samples", action="store_true")
    parser.add_argument("--audio-dir", type=Path, default=ROOT / "var" / "roma" / "asr-public")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    try:
        return asyncio.run(run(args))
    except (
        ValueError,
        KeyError,
        IndexError,
        OSError,
        EOFError,
        wave.Error,
        httpx.HTTPError,
        subprocess.SubprocessError,
    ):
        print(
            "Hosted ASR smoke failed: check the public cache/decoder, endpoint availability and timeout settings; no raw provider details are printed.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
