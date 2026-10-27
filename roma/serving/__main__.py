"""Run only via an explicit cache-only local-model operator action."""

from __future__ import annotations

import argparse
import os
from urllib.parse import urlsplit


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Private cache-only pinned Whisper/Indic-TTS service"
    )
    parser.add_argument(
        "--allow-local-models",
        action="store_true",
        help="Explicitly load/warm the already cached pinned speech models",
    )
    parser.add_argument(
        "--host", choices=("127.0.0.1", "::1"), help="Literal loopback bind override"
    )
    parser.add_argument("--port", type=int, help="Bind port override (1–65535)")
    args = parser.parse_args(argv)
    if args.port is not None and not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    if not args.allow_local_models:
        parser.error("--allow-local-models is required; no models are loaded by default")
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    import uvicorn

    from roma.core.serving_settings import SpeechServingSettings
    from roma.serving.runtime import build_local_speech_app

    settings = SpeechServingSettings()
    settings.require_key()
    url = urlsplit(settings.speech_service_url)
    host = args.host or url.hostname
    port = args.port or url.port
    assert host is not None and port is not None
    bind_host = "[" + host + "]" if ":" in host else host
    settings = settings.model_copy(update={"speech_service_url": f"http://{bind_host}:{port}"})
    url = urlsplit(settings.speech_service_url)
    assert url.hostname is not None and url.port is not None  # Validated literal loopback URL.
    uvicorn.run(
        build_local_speech_app(settings),
        host=url.hostname,
        port=url.port,
        workers=1,
        limit_concurrency=settings.speech_service_max_inflight + 8,
        backlog=16,
        timeout_keep_alive=5,
        timeout_graceful_shutdown=int(settings.speech_service_shutdown_secs),
        proxy_headers=False,
        access_log=False,
    )


if __name__ == "__main__":
    main()
