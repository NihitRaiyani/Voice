"""Roma runtime configuration. Secrets are env-only (docs/07-security.md).

A missing required secret raises at get_settings() — the process refuses to run
unguarded rather than proceed. Secret values are read only via .get_secret_value()
at the exact API-client boundary.
"""

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        env_ignore_empty=True,
    )

    # --- Twilio -------------------------------------------------------------------------
    twilio_account_sid: SecretStr = SecretStr("")
    twilio_auth_token: SecretStr = SecretStr("")
    twilio_from_number: SecretStr = SecretStr("")

    sarvam_api_key: SecretStr
    openai_api_key: SecretStr
    redis_url: SecretStr

    google_calendar_id: str = ""
    google_calendar_api_key: "SecretStr | None" = None

    app_env: str = "dev"
    log_level: str = "INFO"

    # Where Twilio reaches this process. ONE base, https — the `/answer` URL and `/ws`
    # websocket URL are both derived from it, so the two cannot disagree.
    public_base_url: str = "https://localhost:8020"

    enable_barge_in: bool = True

    enable_filler: bool = True

    # Gate 0's DENYLIST for the web endpoint, comma-separated. Was an allowlist
    # (`CONSENTED_NUMBERS`) until 2026-08-04; see `decisions.md`. Any valid Indian mobile now
    # dials unless it is listed here.
    #
    # EMPTY BLOCKS NOTHING, and that is the honest state: no TRAI/DLT feed is wired
    # (docs/07 §consent), so we have no real suppression data. What bounds this endpoint is
    # `api_token`, `max_calls_per_hour`, the spend cap and the calling window — not this.
    dnd_numbers: str = ""

    # Bearer token for /api/call. UNSET MEANS THE ENDPOINT IS DEAD (503), not open: an
    # unconfigured lock must never read as "no lock needed". The dial path rings real phones
    # and spends real budget, so it fails closed.
    #
    # Programmatic backend clients send this in `Authorization: Bearer ...`.
    api_token: "SecretStr | None" = None

    # Bounds the failure the spend cap cannot: a stuck UI retry or a fat-fingered loop costs
    # twenty calls, not the whole budget, and it fails in minutes rather than after the money
    # has gone.
    max_calls_per_hour: int = 20

    # 127.0.0.1 is the DEFAULT, not a runbook line anyone has to remember. /api/call has no
    # real authentication (see `api_token`), so binding wide has to be a deliberate act:
    # BIND_HOST=0.0.0.0, typed by someone who meant it.
    bind_host: str = "127.0.0.1"

    # How long a call's status record outlives the call, for the UI to poll. An hour is well
    # past any call and well under the point where stale rows accumulate.
    call_status_ttl_secs: int = 3600

    roma_data_dir: str = "var/roma"
    recording_enabled: bool = True
    recording_retention_days: int = 90

    # Topped up 100 -> 200 on 2026-08-01 at the user's instruction. The first outbound call
    # (a843a4a7) ended at ₹89.20/100 spent, so the next call would have hit the cap mid-turn.
    # This is a SPEND CEILING, not a target: `roma.domain.costs.spend.SpendLedger` still halts the run.
    openai_budget_inr: float = 200.0

    # 0.75 -> 0.45 on 2026-08-04. This is dead air on EVERY turn, ahead of STT, the LLM and
    # TTS: on live call 932b6c88 the lead's last word to Roma's first audio was ~3.6s and
    # this knob was the first 750ms of it. pipecat rounds it to whole 32ms analyzer frames,
    # so the real move is 736ms -> 448ms, returning ~288ms per turn.
    #
    # The cost is bounded and measured: a mid-sentence pause between 448ms and 736ms now
    # ends the turn where it used to be ridden out. `tests/telephony/test_vad_endpoint.py`
    # pins both the saving and that risk window. Whether real code-mix pauses fall inside it
    # is a live-call question — if Roma starts cutting leads off, raise this back via env
    # (`VAD_STOP_SECS`) before touching any code.
    vad_stop_secs: float = 0.45
    # docs/05 Layer 2. These MIRROR `telephony.backchannel`, which owns the values; they are
    # literals here rather than an import because `config` must not depend on `telephony`.
    # `test_settings_defaults_match_the_docs05_reference_constants` already holds the two in
    # step, so change both together. Restored to docs/05 on 2026-07-31 after b5273f93 — the
    # lowered values had Roma answering before Sarvam's final arrived, and the late final
    # then interrupted her. See `test_endpoint_spans_are_the_live_tuned_numbers`.
    endpoint_terminal_secs: float = 0.50
    endpoint_default_secs: float = 0.85
    endpoint_continuation_secs: float = 1.30
    backchannel_max_secs: float = 0.60

    vad_confidence: float = 0.8
    vad_min_volume: float = 0.7


def require_reachable_base_url(settings: "Settings | None" = None) -> None:
    """Raise unless `PUBLIC_BASE_URL` names a host the carrier can actually reach.

    Twilio fetches `/answer` and opens `/ws` from its own network. Left on this module's
    default (`https://localhost:8020`) — i.e. the tunnel URL was never passed at launch —
    those requests cannot reach Roma.

    Called by the SERVER entrypoint (`scripts/serve_media.py`), not by `build_media_app`: the
    offline tests construct the app directly and localhost is exactly right for them. What
    must never boot unreachable is the thing a carrier is about to call.
    """
    from urllib.parse import urlparse

    settings = settings or get_settings()
    base = settings.public_base_url
    if (urlparse(base).hostname or "").casefold() in {"localhost", "127.0.0.1", "::1", ""}:
        raise RuntimeError(
            f"PUBLIC_BASE_URL is {base!r} — Twilio cannot reach `/answer` or `/ws` there. "
            "Start the tunnel first and pass its URL:\n"
            "  PUBLIC_BASE_URL=https://<sub>.trycloudflare.com .venv/bin/uvicorn "
            "scripts.serve_media:create_app --factory --host 0.0.0.0 --port 8020"
        )


@lru_cache
def get_settings() -> Settings:
    """Load and validate settings once. Raises ValidationError if a required
    secret is missing (docs/07 fail-safe: never run unguarded)."""
    return Settings()
