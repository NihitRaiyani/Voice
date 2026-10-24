"""Roma-issued freshness capabilities, separate from Twilio request signatures.

Twilio's HMAC has no timestamp. Outbound answer intents cover ringing/retries;
stream capabilities cover only admission. Redis consumes the call identity across
workers, with no local fallback. Neither token is a human API credential.
"""

import asyncio
import base64
import hashlib
import hmac
import time
from collections.abc import Callable
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints, TypeAdapter, model_validator

ANSWER_TTL_SECS = 300
STREAM_TTL_SECS = 120
# Cover the entire answer-retry window plus the last issued stream capability.
STREAM_REPLAY_TTL_SECS = 600

Sid = Annotated[str, StringConstraints(pattern=r"^CA[0-9a-fA-F]{32}$")]
Account = Annotated[str, StringConstraints(pattern=r"^AC[0-9a-fA-F]{32}$")]
Lead = Annotated[
    str, StringConstraints(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
]


class _Claims(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", hide_input_in_errors=True)
    purpose: Literal["answer", "stream"]
    account: Account
    call: Sid | None
    lead: Lead | None
    issued: int
    expires: int

    @model_validator(mode="after")
    def bind_purpose(self):
        if (self.purpose == "stream") != (self.call is not None):
            raise ValueError("invalid capability identity")
        return self


def _encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


class CallCapabilities:
    def __init__(self, secret: str, account: str, *, clock: Callable[[], float] = time.time):
        if not secret:
            raise ValueError("provider security unavailable")
        self._key = hmac.digest(secret.encode(), b"roma:twilio:capabilities:v1", "sha256")
        self._account = TypeAdapter(Account).validate_python(account)
        self._clock = clock

    def _issue(
        self, purpose: Literal["answer", "stream"], call: str | None, lead: str | None
    ) -> str:
        now = int(self._clock())
        claims = _Claims(
            purpose=purpose,
            account=self._account,
            call=call,
            lead=lead,
            issued=now,
            expires=now + (ANSWER_TTL_SECS if purpose == "answer" else STREAM_TTL_SECS),
        )
        body = _encode(claims.model_dump_json().encode())
        return body + "." + _encode(hmac.digest(self._key, body.encode(), "sha256"))

    def _verify(
        self,
        token: str,
        purpose: Literal["answer", "stream"],
        call: str | None,
        lead: str | None,
    ) -> None:
        try:
            if not isinstance(token, str) or not 1 <= len(token) <= 1024:
                raise ValueError
            body, signature = token.split(".")
            expected = _encode(hmac.digest(self._key, body.encode("ascii"), "sha256"))
            if not hmac.compare_digest(signature, expected):
                raise ValueError
            claims = _Claims.model_validate_json(
                base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
            )
            now = int(self._clock())
            ttl = ANSWER_TTL_SECS if purpose == "answer" else STREAM_TTL_SECS
            if (
                claims.purpose != purpose
                or claims.account != self._account
                or claims.call != call
                or claims.lead != lead
                or not claims.issued <= now < claims.expires
                or claims.expires - claims.issued != ttl
            ):
                raise ValueError
        except (ValueError, TypeError, UnicodeError) as exc:
            raise ValueError("invalid provider capability") from exc

    def issue_answer(self, lead: str | None) -> str:
        return self._issue("answer", None, lead)

    def verify_answer(self, token: str, lead: str | None) -> None:
        self._verify(token, "answer", None, lead)

    def issue_stream(self, call: str, lead: str | None) -> str:
        return self._issue("stream", call, lead)

    def verify_stream(self, token: str, call: str, lead: str | None) -> None:
        self._verify(token, "stream", call, lead)


class RedisStreamReplay:
    """Admit one stream per call for the bounded answer retry and stream-admission horizon."""

    def __init__(self, redis_url: str | None = None, *, client=None):
        self._url, self._client = redis_url, client

    async def consume(self, account: str, call: str) -> bool:
        key = (
            "roma:stream-admission:" + hashlib.sha256(f"{account}:{call}".encode()).hexdigest()
        )
        async with asyncio.timeout(1):
            if self._client is not None:
                return bool(
                    await self._client.set(key, "1", nx=True, ex=STREAM_REPLAY_TTL_SECS)
                )
            if not self._url:
                raise RuntimeError("stream replay protection unavailable")
            from redis.asyncio import Redis

            async with Redis.from_url(self._url) as client:
                return bool(await client.set(key, "1", nx=True, ex=STREAM_REPLAY_TTL_SECS))
