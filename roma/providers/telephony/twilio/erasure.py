"""Restricted-key, account-scoped known-resource Twilio SDK erasure adapter.

No list/discovery, dial credentials, implicit retries, redirects or response logging.
"""

import logging
import re
from typing import Any

from twilio.base.exceptions import TwilioRestException
from twilio.http.async_http_client import AsyncTwilioHttpClient
from twilio.rest import Client

from roma.core.erasure_contracts import (
    ProviderFailure,
    ResourceKind,
    ResourceMetadata,
)


class _ErasureHttp(AsyncTwilioHttpClient):
    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        if self.session is not None:
            # Locked aiohttp also retries persistent-connection failures for
            # idempotent methods (including DELETE), independently of SDK retries.
            # Its installed API has no public switch; pin this behavior by test.
            self.session._retry_connection = False

    async def request(self, *args: Any, **kwargs: Any) -> Any:
        kwargs["allow_redirects"] = False
        try:
            response = await super().request(*args, **kwargs)
            if 300 <= response.status_code < 400:
                raise ProviderFailure("provider_manual")
            return response
        finally:
            # The SDK's debugging snapshots otherwise retain response bodies, URLs
            # and BasicAuth. They must not outlive this one request.
            self._test_only_last_request = None
            self._test_only_last_response = None


def _failure(status: int | None) -> ProviderFailure:
    if status == 404:
        return ProviderFailure("absent")
    if status in {401, 403}:
        return ProviderFailure("authentication_manual")
    if status == 429 or (status is not None and 500 <= status < 600):
        return ProviderFailure("transient_pending")
    return ProviderFailure("provider_manual")


class TwilioErasureProvider:
    def __init__(
        self,
        account_sid: str,
        api_key_sid: str,
        api_key_secret: str,
        *,
        http_client: Any = None,
    ):
        if (
            re.fullmatch(r"AC[0-9a-fA-F]{32}", account_sid) is None
            or re.fullmatch(r"SK[0-9a-fA-F]{32}", api_key_sid) is None
            or not 16 <= len(api_key_secret) <= 256
            or any(character.isspace() for character in api_key_secret)
        ):
            raise ValueError("Separate restricted erasure API credentials required.")
        self._account_sid = account_sid
        logger = logging.Logger("roma.erasure.transport", level=logging.CRITICAL + 1)
        logger.disabled, logger.propagate = True, False
        # None means the SDK's simple non-retrying aiohttp client. max_retries=0
        # would construct a RetryClient; deliberately do not instantiate one.
        self._http = (
            http_client
            if http_client is not None
            else _ErasureHttp(
                timeout=3,
                max_retries=None,
                logger=logger,
            )
        )
        self._owns_http = http_client is None
        self._client = Client(
            api_key_sid,
            api_key_secret,
            account_sid=account_sid,
            http_client=self._http,
            environment={"ROMA_ERASURE_EXPLICIT": "1"},
        )

    @property
    def account_sid(self) -> str:
        return self._account_sid

    def _context(self, kind: ResourceKind, sid: str) -> Any:
        prefixes = {"call": "CA", "recording": "RE", "transcription": "TR"}
        if (
            kind not in prefixes
            or re.fullmatch(prefixes[kind] + r"[0-9a-fA-F]{32}", sid) is None
        ):
            raise ProviderFailure("ownership_or_state_denied")
        account = self._client.api.v2010.accounts(self.account_sid)
        if kind == "call":
            return account.calls(sid)
        if kind == "recording":
            return account.recordings(sid)
        return account.transcriptions(sid)

    async def fetch(self, kind: ResourceKind, sid: str) -> ResourceMetadata:
        try:
            resource = await self._context(kind, sid).fetch_async()
            # Never retain text, phone, media URLs or the SDK resource itself.
            return ResourceMetadata(
                resource.account_sid,
                resource.sid,
                resource.status,
                call_sid=resource.call_sid if kind == "recording" else None,
                recording_sid=resource.recording_sid if kind == "transcription" else None,
            )
        except TwilioRestException as exc:
            raise _failure(exc.status) from None
        except TimeoutError:
            raise ProviderFailure("transient_pending") from None
        except ProviderFailure:
            raise
        except Exception:  # noqa: BLE001 — no raw SDK text crosses this boundary
            raise ProviderFailure("provider_manual") from None

    async def delete(self, kind: ResourceKind, sid: str) -> None:
        try:
            if not await self._context(kind, sid).delete_async():
                raise ProviderFailure("provider_manual")
        except TwilioRestException as exc:
            raise _failure(exc.status) from None
        except TimeoutError:
            raise ProviderFailure("transient_pending") from None
        except ProviderFailure:
            raise
        except Exception:  # noqa: BLE001 — no raw SDK text crosses this boundary
            raise ProviderFailure("provider_manual") from None

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.close()
