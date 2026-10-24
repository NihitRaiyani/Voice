"""Fail-closed explicit, call-bound recording evidence; wording alone is insufficient."""

import asyncio
import inspect
from datetime import UTC, datetime

from roma.domain.calls import consent_signed_off


def recording_consent_valid(job, now=None):
    now = now or datetime.now(UTC)
    try:
        consent = datetime.fromisoformat(job.recording_consent_at)
        expiry = datetime.fromisoformat(job.recording_retention_until)
        end = datetime.fromisoformat(job.ended_at)
        return (
            bool(job.recording_disclosure_version)
            and consent.tzinfo is not None
            and expiry.tzinfo is not None
            and consent <= end
            and consent <= now < expiry
        )
    except (ValueError, TypeError, AttributeError):
        return False


async def verified_recording_consent(provider, call_sid):
    """Trusted injected provider returns explicit evidence, never inferred from speech."""
    if not consent_signed_off() or not callable(provider):
        return None
    try:
        async with asyncio.timeout(1):
            if inspect.iscoroutinefunction(provider):
                evidence = await provider(call_sid)
            else:
                evidence = await asyncio.to_thread(provider, call_sid)
                if inspect.isawaitable(evidence):
                    evidence = await evidence
        now = datetime.now(UTC)
        if (
            not isinstance(evidence, dict)
            or evidence.get("call_sid") != call_sid
            or evidence.get("granted") is not True
            or not isinstance(evidence.get("disclosure_version"), str)
            or not evidence["disclosure_version"].strip()
        ):
            return None
        consent = datetime.fromisoformat(evidence["granted_at"])
        expiry = datetime.fromisoformat(evidence["retention_until"])
        if consent.tzinfo is None or expiry.tzinfo is None or not consent <= now < expiry:
            return None
        return dict(evidence)
    except Exception:  # noqa: BLE001 — injected consent lookup failure never authorizes capture
        return None
