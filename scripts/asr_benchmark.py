"""Public FLEURS benchmark: prepare, run/resume, inspect/export failures and replay.

Network is opt-in, public Spaces receive no token, and no local weights are loaded.
"""

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from roma.core.speech_settings import SpeechSettings  # noqa: E402
from roma.eval.asr_benchmark import (  # noqa: E402
    LANGUAGES,
    VARIANTS,
    build_manifest,
    export_failure_samples,
    failure_samples,
    read_json,
    run_benchmark,
    write_json,
)
from roma.providers.ai.hf_asr import (  # noqa: E402
    GradioIndicSTT,
    GradioWhisperSTT,
    HFHostedSTT,
)


async def fetch_public_samples(audio_dir: Path, count: int) -> None:
    # Reuse the existing bounded transport and native audio-only decoder.
    import httpx
    from hf_asr_smoke import bounded_get, decode_flac

    if not 1 <= count <= 20:
        raise ValueError("Choose one to twenty public rows per language")
    await asyncio.to_thread(audio_dir.mkdir, parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=30, trust_env=False, follow_redirects=False) as client:
        for language, config in LANGUAGES.items():
            for index in range(count):
                stem = language if index == 0 else f"{language}-{index:04d}"
                wav, metadata_path = audio_dir / (stem + ".wav"), audio_dir / (stem + ".json")
                if wav.exists() and metadata_path.exists():
                    continue  # build_manifest verifies every cached identity before use.
                raw = await bounded_get(
                    client,
                    "https://datasets-server.huggingface.co/rows",
                    limit=1024 * 1024,
                    params={
                        "dataset": "google/fleurs",
                        "config": config,
                        "split": "validation",
                        "offset": index,
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
                    or parsed.port not in (None, 443)
                ):
                    raise ValueError("Unexpected public FLEURS audio origin")
                payload = await bounded_get(client, audio_url, limit=2 * 1024 * 1024)
                decode_flac(payload, wav)
                write_json(
                    metadata_path,
                    {
                        "dataset": "google/fleurs",
                        "config": config,
                        "split": "validation",
                        "row": index,
                        "reference": row["transcription"],
                        "license": "CC-BY-4.0",
                        "sha256": hashlib.sha256(wav.read_bytes()).hexdigest(),
                    },
                )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    sub = result.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser(
        "prepare", help="Verify local public cache and write a deterministic manifest"
    )
    prepare.add_argument("--allow-network", action="store_true")
    prepare.add_argument("--fetch-public-samples", action="store_true")
    prepare.add_argument("--samples-per-language", type=int, default=1)
    prepare.add_argument("--include-stitched-code-mix", action="store_true")
    prepare.add_argument("--variants", nargs="+", choices=VARIANTS, default=list(VARIANTS))
    prepare.add_argument("--seed", type=int, default=18)
    prepare.add_argument("--snr-db", type=float, default=15)
    prepare.add_argument("--manifest", type=Path, required=True)
    prepare.add_argument("--audio-dir", type=Path, default=ROOT / "var/roma/asr-public")
    for command in ("run", "replay"):
        run = sub.add_parser(
            command,
            help="Run sequential hosted requests"
            if command == "run"
            else "Replay saved error/high-WER public cases",
        )
        run.add_argument("--allow-network", action="store_true")
        run.add_argument("--manifest", type=Path, required=True)
        run.add_argument("--audio-dir", type=Path, default=ROOT / "var/roma/asr-public")
        run.add_argument("--report", type=Path, required=True)
        run.add_argument(
            "--provider",
            choices=("hf_public_lab", "hf_indic", "hf_whisper_space"),
            default="hf_public_lab",
        )
        run.add_argument("--languages", nargs="+", choices=(*LANGUAGES, "gu-en", "hi-en"))
        run.add_argument("--resume", action="store_true")
        run.add_argument("--retry-errors", action="store_true")
        run.add_argument("--min-wer", type=float, default=0.25)
    failures = sub.add_parser(
        "failures", help="Read failure text/metrics offline; optionally export public WAVs"
    )
    failures.add_argument("--report", type=Path, required=True)
    failures.add_argument("--min-wer", type=float, default=0.25)
    failures.add_argument("--manifest", type=Path)
    failures.add_argument("--audio-dir", type=Path, default=ROOT / "var/roma/asr-public")
    failures.add_argument("--export-dir", type=Path)
    return result


async def run(args) -> int:
    if args.command == "prepare":
        if args.fetch_public_samples:
            if not args.allow_network:
                raise ValueError("Fetching public samples requires --allow-network")
            await fetch_public_samples(args.audio_dir, args.samples_per_language)
        manifest = build_manifest(
            args.audio_dir,
            include_stitched_code_mix=args.include_stitched_code_mix,
            variants=tuple(args.variants),
            seed=args.seed,
            snr_db=args.snr_db,
        )
        write_json(args.manifest, manifest)
        print(
            json.dumps(
                {
                    "manifest": str(args.manifest),
                    "cases": len(manifest["cases"]),
                    "sources": len(manifest["sources"]),
                }
            )
        )
        return 0
    if args.command == "failures":
        report = read_json(args.report)
        rows = failure_samples(report, min_wer=args.min_wer)
        if args.export_dir:
            if not args.manifest:
                raise ValueError("Exporting failure audio requires --manifest")
            export_failure_samples(
                report,
                read_json(args.manifest),
                args.audio_dir,
                args.export_dir,
                min_wer=args.min_wer,
            )
        print(
            json.dumps(
                {"schema": "roma-asr-failures-v1", "cases": rows}, ensure_ascii=False, indent=2
            )
        )
        return 0
    if not args.allow_network:
        raise ValueError("Hosted benchmark requests require --allow-network")
    manifest = read_json(args.manifest)
    languages = (
        set(args.languages)
        if args.languages
        else {case["language"] for case in manifest["cases"]}
    )
    saved = read_json(args.report) if args.command == "replay" else None
    if saved is not None and args.languages is None:
        languages = set(saved["languages"])
    if args.provider == "hf_indic" and "en" in languages:
        raise ValueError("Indic does not support English; select --languages gu hi gu-en hi-en")
    config = SpeechSettings(stt_provider="hf_public_lab")
    options = {
        "timeout_secs": config.hf_public_timeout_secs,
        "max_audio_secs": config.stt_max_audio_secs,
    }
    if args.provider == "hf_indic":
        provider = GradioIndicSTT(
            endpoint_url=config.hf_indic_space_url, decoder=config.indic_decoder, **options
        )
    elif args.provider == "hf_whisper_space":
        provider = GradioWhisperSTT(endpoint_url=config.hf_whisper_space_url, **options)
    else:
        provider = HFHostedSTT(
            endpoint_url=config.hf_indic_space_url,
            whisper_endpoint_url=config.hf_whisper_space_url,
            decoder=config.indic_decoder,
            **options,
        )
    case_ids = None
    if args.command == "replay":
        case_ids = {row["case_id"] for row in failure_samples(saved, min_wer=args.min_wer)}
        if not case_ids:
            print(
                json.dumps(
                    {"replayed": 0, "reason": "No saved cases match the failure threshold"}
                )
            )
            return 0
    details = {
        "indic_endpoint": config.hf_indic_space_url,
        "whisper_endpoint": config.hf_whisper_space_url,
        "decoder": config.indic_decoder,
        "timeout_secs": config.hf_public_timeout_secs,
        "remote_model_revision": None,
        "model_provenance": "Public Space deployment; remote model revision unverified",
    }
    report = await run_benchmark(
        manifest,
        args.audio_dir,
        provider,
        provider_name=args.provider,
        report_path=args.report,
        resume=args.resume or args.command == "replay",
        retry_errors=args.retry_errors,
        case_ids=case_ids,
        languages=languages,
        provider_details=details,
    )
    print(
        json.dumps(
            {
                "report": str(args.report),
                "complete": report["complete"],
                "summaries": report["summaries"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return (
        1
        if not report["complete"] or any(row["status"] != "ok" for row in report["cases"])
        else 0
    )


def main() -> int:
    args = parser().parse_args()
    try:
        return asyncio.run(run(args))
    except Exception:  # noqa: BLE001 -- fixed CLI boundary; suppress raw remote details.
        print(
            "ASR benchmark failed: verify public cache/manifest identity, selection, network opt-in and endpoint availability. No raw provider details are printed.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
