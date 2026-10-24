"""User-bound short-lived local recording access with mandatory durable audit."""

import asyncio
import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from roma.core.field_encryption import PrivacyError, secret_key
from roma.core.privacy_paths import read_private_recording, safe_runtime_path


class RecordingUnavailable(RuntimeError):
    def __init__(self):
        super().__init__("Recording access is unavailable.")


@dataclass(frozen=True)
class RecordingAccess:
    id: UUID
    call_id: UUID
    object_key: str
    media_type: str
    retention_until: datetime
    consent_at: datetime | None
    status: str
    deleted: bool = False


class RecordingService:
    def __init__(self, repository, recording_root, settings, *, clock=None):
        self.repository = repository
        self.root = recording_root
        self.settings = settings
        self.clock = clock or (lambda: datetime.now(UTC))

    def _key(self):
        return secret_key(self.settings.recording_url_signing_key)

    async def _available(self, recording_id):
        row = await self.repository.recording(recording_id)
        if (
            row is None
            or row.deleted
            or row.status != "available"
            or row.consent_at is None
            or row.retention_until <= self.clock()
        ):
            raise RecordingUnavailable()
        return row

    async def issue(self, recording_id, actor_id, **context):
        try:
            row = await self._available(recording_id)
            expiry = min(
                int(self.clock().timestamp()) + self.settings.recording_url_ttl_secs,
                int(row.retention_until.timestamp()),
            )
            encoded = (
                base64.urlsafe_b64encode(
                    json.dumps(
                        {
                            "r": str(recording_id),
                            "u": str(actor_id),
                            "exp": expiry,
                            "p": "recording",
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode()
                )
                .decode()
                .rstrip("=")
            )
            signed = hmac.new(self._key(), encoded.encode(), hashlib.sha256).hexdigest()
            await self.repository.record_access(
                row, actor_id, "recording.link_issued", now=self.clock(), **context
            )
            return {
                "url": f"/api/v1/recordings/{recording_id}/content?token={encoded}.{signed}",
                "expires_at": datetime.fromtimestamp(expiry, UTC).isoformat(),
            }
        except (SQLAlchemyError, PrivacyError, OSError, ValueError, TypeError, TimeoutError):
            raise RecordingUnavailable() from None

    async def play(self, recording_id, actor_id, token, **context):
        try:
            if not isinstance(token, str) or len(token) > 1024:
                raise ValueError
            encoded, signature = token.split(".")
            expected = hmac.new(
                self._key(), encoded.encode("ascii"), hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(signature, expected):
                raise ValueError
            claims = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
            if (
                claims.get("r") != str(recording_id)
                or claims.get("u") != str(actor_id)
                or claims.get("p") != "recording"
                or type(claims.get("exp")) is not int
                or claims["exp"] <= int(self.clock().timestamp())
            ):
                raise ValueError
            row = await self._available(recording_id)
            path = safe_runtime_path(self.root, row.object_key)
            data = await asyncio.to_thread(read_private_recording, path, root=self.root)
            # Repository rechecks deletion/expiry under the same audit transaction.
            await self.repository.record_access(
                row, actor_id, "recording.playback", now=self.clock(), **context
            )
            return data, row.media_type
        except (
            SQLAlchemyError,
            PrivacyError,
            OSError,
            ValueError,
            UnicodeError,
            TypeError,
            AttributeError,
            TimeoutError,
        ):
            raise RecordingUnavailable() from None
