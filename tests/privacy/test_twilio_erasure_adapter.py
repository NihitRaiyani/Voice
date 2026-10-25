"""Exercise the real installed SDK through an offline HTTP boundary."""

import asyncio
import json

import pytest
from roma.providers.telephony.twilio.erasure import TwilioErasureProvider
from twilio.http.response import Response

ACCOUNT = "AC" + "1" * 32
KEY = "SK" + "9" * 32
SECRET = "fake-restricted-key-secret"
CALL = "CA" + "2" * 32
RECORDING = "RE" + "3" * 32
TRANSCRIPTION = "TR" + "4" * 32


class Transport:
    is_async = True

    def __init__(self, status=200):
        self.requests = []
        self.status = status

    async def request(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))
        if method == "DELETE":
            return Response(204 if self.status == 200 else self.status, "secret-text")
        sid = url.split("/")[-1].removesuffix(".json")
        return Response(
            self.status,
            json.dumps(
                {
                    "account_sid": ACCOUNT,
                    "sid": sid,
                    "call_sid": CALL,
                    "recording_sid": RECORDING,
                    "status": "completed",
                    "transcription_text": "SECRET TRANSCRIPT",
                    "to": "+919999999999",
                }
            ),
        )


def test_sdk_uses_distinct_account_scoped_endpoints_and_projects_only_metadata(monkeypatch):
    async def scenario():
        monkeypatch.setenv("TWILIO_EDGE", "untrusted")
        transport = Transport()
        provider = TwilioErasureProvider(ACCOUNT, KEY, SECRET, http_client=transport)
        for kind, sid, endpoint in (
            ("transcription", TRANSCRIPTION, "Transcriptions"),
            ("recording", RECORDING, "Recordings"),
            ("call", CALL, "Calls"),
        ):
            resource = await provider.fetch(kind, sid)
            assert resource.account_sid == ACCOUNT and resource.sid == sid
            assert "SECRET" not in repr(resource) and "9999999999" not in repr(resource)
            await provider.delete(kind, sid)
            expected = (
                f"https://api.twilio.com/2010-04-01/Accounts/{ACCOUNT}/{endpoint}/{sid}.json"
            )
            assert transport.requests[-1][1] == expected
        assert [request[0] for request in transport.requests] == ["GET", "DELETE"] * 3
        assert all(request[2]["auth"] == (KEY, SECRET) for request in transport.requests)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "status,outcome",
    [
        (404, "absent"),
        (401, "authentication_manual"),
        (403, "authentication_manual"),
        (429, "transient_pending"),
        (503, "transient_pending"),
        (302, "provider_manual"),
        (400, "provider_manual"),
    ],
)
def test_sdk_failures_are_fixed_sanitized_and_never_retry(status, outcome):
    from roma.core.erasure_contracts import ProviderFailure

    async def scenario():
        transport = Transport(status)
        provider = TwilioErasureProvider(ACCOUNT, KEY, SECRET, http_client=transport)
        with pytest.raises(ProviderFailure) as caught:
            await provider.delete("call", CALL)
        assert caught.value.code == outcome and "secret" not in str(caught.value)
        assert len(transport.requests) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("status", [200, 302])
def test_http_transport_forbids_redirects_and_discards_sdk_debug_snapshots(
    status, monkeypatch, caplog
):
    import logging

    from roma.core.erasure_contracts import ProviderFailure
    from roma.providers.telephony.twilio.erasure import _ErasureHttp
    from twilio.http.async_http_client import AsyncTwilioHttpClient

    async def scenario():
        calls = []

        async def request(self, *args, **kwargs):
            calls.append(kwargs)
            self._test_only_last_request = "secret credential"
            self._test_only_last_response = "SECRET TRANSCRIPT"
            self.logger.warning("secret credential transcript")
            return Response(status, "SECRET TRANSCRIPT")

        monkeypatch.setattr(AsyncTwilioHttpClient, "request", request)
        logger = logging.Logger("erasure-test")
        logger.disabled = True
        http = _ErasureHttp(pool_connections=False, timeout=3, max_retries=None, logger=logger)
        if status == 302:
            with pytest.raises(ProviderFailure):
                await http.request("GET", "https://api.twilio.com/", allow_redirects=True)
        else:
            await http.request("GET", "https://api.twilio.com/", allow_redirects=True)
        assert len(calls) == 1 and calls[0]["allow_redirects"] is False
        assert http._test_only_last_request is None and http._test_only_last_response is None
        assert "SECRET" not in caplog.text and "secret credential" not in caplog.text

    asyncio.run(scenario())


def test_adapter_does_not_accept_account_auth_token_as_api_key():
    with pytest.raises(ValueError):
        TwilioErasureProvider(ACCOUNT, ACCOUNT, "fake-dial-auth-token")


def test_default_transport_disables_sdk_and_aiohttp_persistent_connection_retry():
    async def scenario():
        provider = TwilioErasureProvider(ACCOUNT, KEY, SECRET)
        try:
            assert provider._http.session._retry_connection is False
            assert provider._http.logger.disabled and not provider._http.logger.propagate
            assert provider._http.timeout == 3
        finally:
            await provider.aclose()

    asyncio.run(scenario())
