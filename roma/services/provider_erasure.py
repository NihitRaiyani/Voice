"""Bounded known-resource erasure. Preview is pure; no account inventory is inferred."""

import asyncio
import json
import math
import re
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from roma.core.erasure_contracts import ErasureProvider, ProviderFailure, ResourceKind
from roma.services.erasure_ledger import ErasureLedger, resource_key

MANUAL_PENDING = [
    "twilio_unknown_inventory_archives_and_other_products",
    "anthropic_account_model_feature_retention_confirmation",
    "sarvam_supported_request_and_retention_confirmation",
    "backups_replicas_and_operator_exports",
]


def _sid(value: object, prefix: str) -> bool:
    return (
        isinstance(value, str) and re.fullmatch(prefix + r"[0-9a-fA-F]{32}", value) is not None
    )


@dataclass(frozen=True)
class TranscriptionTarget:
    sid: str
    recording_sid: str


@dataclass(frozen=True)
class ErasureManifest:
    account_sid: str
    call_sid: str
    recording_sids: tuple[str, ...]
    transcriptions: tuple[TranscriptionTarget, ...]

    def __post_init__(self) -> None:
        if (
            not _sid(self.account_sid, "AC")
            or not _sid(self.call_sid, "CA")
            or type(self.recording_sids) is not tuple
            or type(self.transcriptions) is not tuple
            or len(self.recording_sids) > 50
            or len(self.transcriptions) > 50
            or any(not _sid(sid, "RE") for sid in self.recording_sids)
            or len(set(self.recording_sids)) != len(self.recording_sids)
            or any(
                not isinstance(item, TranscriptionTarget)
                or not _sid(item.sid, "TR")
                or item.recording_sid not in self.recording_sids
                for item in self.transcriptions
            )
            or len({item.sid for item in self.transcriptions}) != len(self.transcriptions)
        ):
            raise ValueError("Invalid bounded provider manifest.")

    @classmethod
    def parse(cls, raw: object) -> "ErasureManifest":
        if not isinstance(raw, dict) or set(raw) != {
            "version",
            "account_sid",
            "call_sid",
            "recording_sids",
            "transcriptions",
        }:
            raise ValueError("Invalid bounded provider manifest.")
        if type(raw["version"]) is not int or raw["version"] != 1:
            raise ValueError("Unsupported provider manifest version.")
        recordings, transcriptions = raw["recording_sids"], raw["transcriptions"]
        if not isinstance(recordings, list) or not isinstance(transcriptions, list):
            raise ValueError("Invalid bounded provider manifest.")
        if any(
            not isinstance(item, dict) or set(item) != {"sid", "recording_sid"}
            for item in transcriptions
        ):
            raise ValueError("Invalid transcription mapping.")
        return cls(
            raw["account_sid"],
            raw["call_sid"],
            tuple(recordings),
            tuple(
                TranscriptionTarget(item["sid"], item["recording_sid"])
                for item in transcriptions
            ),
        )

    def targets(self) -> list[tuple[ResourceKind, str]]:
        return (
            [
                ("transcription", item.sid)
                for item in sorted(self.transcriptions, key=lambda item: item.sid)
            ]
            + [("recording", sid) for sid in sorted(self.recording_sids)]
            + [("call", self.call_sid)]
        )

    @property
    def digest(self) -> str:
        raw = [
            1,
            self.account_sid,
            self.call_sid,
            sorted(self.recording_sids),
            sorted((item.sid, item.recording_sid) for item in self.transcriptions),
        ]
        return sha256(json.dumps(raw, separators=(",", ":")).encode()).hexdigest()


class ProviderErasure:
    def __init__(
        self,
        account_sid: str,
        *,
        provider: ErasureProvider | None = None,
        request_timeout: float = 3,
        total_timeout: float = 30,
    ):
        if not _sid(account_sid, "AC"):
            raise ValueError("Explicit erasure account is required.")
        if (
            not math.isfinite(request_timeout)
            or not math.isfinite(total_timeout)
            or not 0 < request_timeout <= 10
            or not 0 < total_timeout <= 120
        ):
            raise ValueError("Finite bounded erasure deadlines are required.")
        if provider is not None and provider.account_sid != account_sid:
            raise ValueError("Provider account mismatch.")
        self.account_sid, self.provider = account_sid, provider
        self.request_timeout, self.total_timeout = request_timeout, total_timeout

    def preview(self, manifest: ErasureManifest) -> dict:
        if manifest.account_sid != self.account_sid:
            raise ValueError("Provider account mismatch.")
        return {
            "mode": "preview",
            "outcome": "preview_only",
            "counts": {
                "calls": 1,
                "recordings": len(manifest.recording_sids),
                "transcriptions": len(manifest.transcriptions),
            },
            "physical_erasure_complete": False,
            "manual_pending": list(MANUAL_PENDING),
            "provider_retention_confirmation": {
                "anthropic": {
                    "account_confirmed": False,
                    "configured_model_and_features_confirmed": False,
                    "agreement_confirmed": False,
                },
                "sarvam": {"account_and_retention_confirmed": False},
            },
        }

    async def _fetch_verified(
        self,
        manifest: ErasureManifest,
        *,
        previously_verified: bool = False,
    ) -> set[str]:
        if self.provider is None:
            raise ProviderFailure("authentication_manual")
        absent: set[str] = set()
        mappings = {item.sid: item.recording_sid for item in manifest.transcriptions}
        # Validate ALL targets before allowing the first DELETE.
        for kind, sid in reversed(manifest.targets()):
            try:
                async with asyncio.timeout(self.request_timeout):
                    resource = await self.provider.fetch(kind, sid)
            except ProviderFailure as exc:
                if exc.code == "absent":
                    if previously_verified:
                        absent.add(sid)
                        continue
                    raise ProviderFailure("ownership_unverified") from None
                raise
            if (
                resource.account_sid != self.account_sid
                or resource.sid != sid
                or (
                    kind == "call"
                    and resource.status
                    not in {"completed", "busy", "failed", "no-answer", "canceled"}
                )
                or (
                    kind == "recording"
                    and (
                        resource.call_sid != manifest.call_sid or resource.status != "completed"
                    )
                )
                or (
                    kind == "transcription"
                    and (
                        resource.recording_sid != mappings[sid]
                        or resource.status != "completed"
                    )
                )
            ):
                raise ProviderFailure("ownership_or_state_denied")
        return absent

    async def verify(self, manifest: ErasureManifest) -> dict:
        result = {**self.preview(manifest), "mode": "verify"}
        try:
            async with asyncio.timeout(self.total_timeout):
                await self._fetch_verified(manifest)
        except ProviderFailure as exc:
            return {**result, "outcome": exc.code}
        except TimeoutError:
            return {**result, "outcome": "transient_pending"}
        except Exception:  # noqa: BLE001 — never publish provider payloads/exceptions
            return {**result, "outcome": "provider_manual"}
        return {
            **result,
            "outcome": "verified_known_resources",
            "verified_manifest_digest": manifest.digest,
        }

    async def apply(
        self,
        manifest: ErasureManifest,
        *,
        apply: bool,
        verified_digest: str,
        ledger: Path,
    ) -> dict:
        result = {**self.preview(manifest), "mode": "apply"}
        if apply is not True or verified_digest != manifest.digest:
            raise ValueError("Explicit apply and matching verified manifest digest required.")
        targets = manifest.targets()
        keys = [resource_key(kind, sid) for kind, sid in targets]
        try:
            with ErasureLedger(
                ledger, digest=manifest.digest, account=self.account_sid, keys=keys
            ) as receipt:
                async with asyncio.timeout(self.total_timeout):
                    absent = await self._fetch_verified(
                        manifest, previously_verified=receipt.existing
                    )
                    # Reset even completed steps if a provider resource reappeared.
                    receipt.data["steps"] = {
                        resource_key(kind, sid): "absent" if sid in absent else "pending"
                        for kind, sid in targets
                    }
                    receipt.save()
                    for kind, sid in targets:
                        if sid in absent:
                            continue
                        key = resource_key(kind, sid)
                        receipt.data["steps"][key] = "attempting"
                        receipt.save()
                        try:
                            assert self.provider is not None
                            async with asyncio.timeout(self.request_timeout):
                                await self.provider.delete(kind, sid)
                            outcome = "deleted"
                        except ProviderFailure as exc:
                            outcome = exc.code
                        except TimeoutError:
                            outcome = "transient_pending"
                        except Exception:  # noqa: BLE001 — sanitized durable retry evidence
                            outcome = "provider_manual"
                        receipt.data["steps"][key] = outcome
                        receipt.save()
                        if outcome not in {"deleted", "absent"}:
                            return {**result, "outcome": outcome}
        except ProviderFailure as exc:
            return {**result, "outcome": exc.code}
        except TimeoutError:
            return {**result, "outcome": "transient_pending"}
        except (OSError, ValueError, TypeError):
            return {**result, "outcome": "ledger_or_configuration_pending"}
        except Exception:  # noqa: BLE001 — fixed outcomes, never provider text
            return {**result, "outcome": "provider_manual"}
        return {**result, "outcome": "known_resources_absent"}
