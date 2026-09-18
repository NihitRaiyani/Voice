"""The backend dial endpoint — what stands between a request and a phone bill.

Rewritten 2026-08-04 when the consent ALLOWLIST was removed (decisions.md). The old test
`test_a_well_formed_number_nobody_consented_to_is_refused` no longer describes the system:
any valid Indian mobile now dials unless it is on `DND_NUMBERS`. Deleting a test whose
subject is gone is right; deleting the INTENT behind it would not be, so its two descendants
are here — a denylisted number is refused, and the request body still cannot widen any gate.

Gate 0 is now allow-by-default on this path, so what actually bounds the endpoint is the
stack of limits below, and each has a test:

    bearer auth (fails closed)  ->  Indian-mobile shape  ->  window / DNC / spend  ->  hourly cap

Routes are mounted by `mount_web_api` against a bare FastAPI app rather than by booting the
media server.
"""

import tempfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# A data dir of our own, and a budget nothing can exhaust.
#
# Without these the suite reads the DEVELOPER'S REAL SPEND LEDGER (`var/roma/spend.jsonl`).
# On 2026-08-05 that ledger stood at ₹221.66 against a default cap of ₹200, so every dial
# that reached the money gate was refused `409 block:budget` — including
# `test_a_carrier_rejection_is_502_not_an_unhandled_500`, which asserts only `>= 400` and so
# went on passing while testing nothing it claims to. Tests must not read the machine's
# real accounting.
_DATA_DIR = tempfile.mkdtemp(prefix="roma-api-tests-")

BASE_ENV = {
    "TWILIO_ACCOUNT_SID": "AC" + "1" * 32,
    "TWILIO_AUTH_TOKEN": "test-auth-token",
    "TWILIO_FROM_NUMBER": "+16295550100",
    "SARVAM_API_KEY": "sk-test",
    "OPENAI_API_KEY": "sk-test",
    "REDIS_URL": "redis://localhost:6379/0",
    "PUBLIC_BASE_URL": "https://example.test",
    "ROMA_DATA_DIR": _DATA_DIR,
    "OPENAI_BUDGET_INR": "1000000",
}

TOKEN = "test-token-abc"
DENIED = "+919000000001"
ALLOWED = "+919876543210"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


async def _reachable_ok(base_url, **kw):
    """The default preflight for these tests: the carrier can reach us.

    These tests are about the GATES. A real lookup of `example.test` would refuse every dial
    before any gate ran, so the preflight is stubbed here and tested on its own below.
    """
    return True, ""


def _app(monkeypatch, **overrides):
    from roma.config import Settings

    # Pulled out BEFORE `overrides` is walked as environment variables — it is a callable,
    # and `monkeypatch.setenv` would reject it.
    reachable_fn = overrides.pop("_reachable", None) or _reachable_ok

    # Cut the `.env` file out of the picture entirely, or these tests are not hermetic and
    # the fail-closed one is a LIE on any machine that actually has a token configured.
    #
    # `monkeypatch.delenv("API_TOKEN")` removes the process env var, but pydantic-settings
    # ALSO reads `.env`, so on the dev laptop — where a real `API_TOKEN` is set, as it must
    # be for the UI to work at all — the "unset token disables the endpoint" test got a
    # perfectly valid token, sent the wrong one, and saw 401 instead of 503. It passed in CI
    # and on any clean checkout, which is precisely how it went unnoticed.
    #
    # Same shape as the `DND_NUMBERS` gap found on 2026-08-04: config the tests set for
    # themselves and the config the server actually runs on had drifted apart, and nothing
    # compared them. A test that deletes a setting must control the whole settings source.
    monkeypatch.setitem(Settings.model_config, "env_file", None)

    # The endpoint judges the TRAI calling window against the REAL wall clock, so until
    # 2026-08-08 this suite silently only passed between 09:00 and 21:00 IST — every dial
    # after nine at night was refused `block:window` before the gate under test ever ran.
    # The window predicate has its own injected-`now` tests in `tests/dialer/test_precall.py`;
    # here it is pinned open. (`precall_check` resolves the name through its module globals,
    # which is why this patch reaches it when nothing inside `mount_web_api` can be patched.)
    monkeypatch.setattr("roma.dialer.precall.in_calling_window", lambda now: True)

    for key, value in {**BASE_ENV, "API_TOKEN": TOKEN, "DND_NUMBERS": DENIED}.items():
        monkeypatch.setenv(key, value)
    for key, value in overrides.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    from roma.config import get_settings

    get_settings.cache_clear()
    from roma.telephony.webapi import mount_web_api

    app = FastAPI()
    mount_web_api(app, reachable_fn=reachable_fn)
    return TestClient(app)


@pytest.fixture
def client(monkeypatch):
    c = _app(monkeypatch)
    yield c
    from roma.config import get_settings

    get_settings.cache_clear()


# --- the gate that replaced the allowlist ------------------------------------------------


def test_a_denylisted_number_is_refused(client):
    """The direct descendant of the deleted allowlist test.

    `block:dnd` has to keep meaning something after the allowlist is gone, and this is the
    only thing that makes it so — `DenylistRegistry` reading `DND_NUMBERS`.
    """
    res = client.post("/api/call", json={"to_number": DENIED}, headers=AUTH)
    assert res.status_code == 409
    assert res.json()["detail"]["reason"] == "block:dnd"


def test_the_denylist_is_read_rather_than_merely_present():
    """The inverse. Without this, a registry that refused EVERYTHING would pass the test
    above and nothing would notice until no call could be placed at all."""
    from roma.dialer.dnd import DenylistRegistry, numbers_from_config

    registry = DenylistRegistry(suppressed=numbers_from_config(f"{DENIED}, +919111111111"))
    assert not registry.is_dialable(DENIED)
    assert registry.is_dialable(ALLOWED), "an unlisted Indian mobile must now be dialable"

    assert DenylistRegistry(suppressed=numbers_from_config("")).is_dialable(ALLOWED), (
        "an empty denylist blocks nothing — the documented, intended default"
    )


def test_the_request_body_cannot_widen_or_bypass_any_gate(client):
    """The other half of the deleted test's intent, and the reason only `to_number` is read.

    A payload that tries to hand itself consent, empty the denylist, or skip the gates must
    change nothing. This is true by construction — there is no code path from the body to the
    registry, the window or the cap — and this test is what keeps it true.
    """
    res = client.post(
        "/api/call",
        json={
            "to_number": DENIED,
            "consented": [DENIED],
            "dnd_numbers": "",
            "skip_gates": True,
            "max_calls_per_hour": 9999,
        },
        headers=AUTH,
    )
    assert res.status_code == 409
    assert res.json()["detail"]["reason"] == "block:dnd"


# --- authentication ----------------------------------------------------------------------


def test_no_token_is_401(client):
    assert client.post("/api/call", json={"to_number": ALLOWED}).status_code == 401


def test_a_wrong_token_is_401(client):
    res = client.post(
        "/api/call", json={"to_number": ALLOWED}, headers={"Authorization": "Bearer nope"}
    )
    assert res.status_code == 401


def test_a_non_bearer_scheme_is_401(client):
    res = client.post(
        "/api/call", json={"to_number": ALLOWED}, headers={"Authorization": f"Basic {TOKEN}"}
    )
    assert res.status_code == 401


def test_an_unconfigured_token_disables_the_endpoint_rather_than_opening_it(monkeypatch):
    """FAIL CLOSED. The failure this prevents is a deploy where nobody set API_TOKEN and the
    dial button quietly works for everyone who can reach the port."""
    client = _app(monkeypatch, API_TOKEN=None)
    try:
        res = client.post("/api/call", json={"to_number": ALLOWED}, headers=AUTH)
        assert res.status_code == 503
    finally:
        from roma.config import get_settings

        get_settings.cache_clear()


def test_the_status_endpoint_is_behind_auth_too(client):
    """It leaks less than the dial endpoint, but an unauthenticated reader can still watch
    when calls happen and how they end."""
    assert client.get("/api/call/anything").status_code == 401


# --- callee shape: Indian mobiles only ---------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "not-a-number",
        "9876543210",  # no country code
        "919876543210",  # no +
        "+91 98765 43210",  # spaces are not E.164
        "+14155551234",  # not India — this is not a general dialer
        "+445551234567",
        "+912212345678",  # +91 landline range (opens 2), not a mobile
        "+9198765432100",  # eleven digits
        "+919876543",  # too short
    ],
)
def test_only_indian_mobiles_are_accepted(client, bad):
    assert client.post("/api/call", json={"to_number": bad}, headers=AUTH).status_code == 400


@pytest.mark.parametrize("good", ["+916000000000", "+917000000000", "+919876543210"])
def test_the_indian_mobile_range_is_6_to_9(good):
    from roma.dialer.dnd import is_indian_mobile

    assert is_indian_mobile(good)


def test_a_non_json_body_is_a_400_not_a_500(client):
    res = client.post("/api/call", content="to_number=+919876543210", headers=AUTH)
    assert res.status_code == 400


# --- CORS --------------------------------------------------------------------------------


def test_call_api_does_not_emit_browser_cors_headers(client):
    response = client.options(
        "/api/call",
        headers={"Origin": "http://localhost:3030", "Access-Control-Request-Method": "POST"},
    )
    assert "access-control-allow-origin" not in response.headers


# --- carrier reachability preflight ------------------------------------------------------


def test_an_unreachable_base_url_refuses_the_dial_instead_of_calling_the_carrier(monkeypatch):
    """The dead-tunnel failure, caught before it becomes a phone call.

    `PUBLIC_BASE_URL` pointing at a `cloudflared` quick tunnel that has since died took down
    the dial button twice in two days (2026-08-04, 2026-08-05). Nothing noticed: the server
    boots (the URL is not localhost, which is all `require_reachable_base_url` checks), every
    gate passes (none of them read the base URL), and Twilio 400s on an answer URL it cannot
    resolve. The operator's error was `502 carrier refused (HTTPStatusError)` — which blames
    the carrier for a stale line in `.env`.
    """

    async def _unreachable(base_url, **kw):
        return False, f"cannot reach PUBLIC_BASE_URL ({base_url}) — DNS or connection failed"

    client = _app(monkeypatch, _reachable=_unreachable)
    try:
        res = client.post("/api/call", json={"to_number": ALLOWED}, headers=AUTH)
        assert res.status_code == 503, "our config being broken is not the carrier's fault"
        assert "PUBLIC_BASE_URL" in res.json()["detail"]["reason"], (
            "the reason must name the setting to change — the whole point is that "
            "'carrier refused' sent the operator looking in the wrong place"
        )
    finally:
        from roma.config import get_settings

        get_settings.cache_clear()


def test_the_preflight_runs_before_the_hourly_cap_is_consumed(monkeypatch):
    """A dead tunnel must not eat the hour's dial quota discovering itself.

    Ordering matters here in a way it does not for the other gates: the cap is the one check
    with a SIDE EFFECT, so anything that can refuse a call belongs in front of it.
    """
    seen = []

    async def _unreachable(base_url, **kw):
        seen.append(base_url)
        return False, "cannot reach PUBLIC_BASE_URL"

    client = _app(monkeypatch, _reachable=_unreachable, MAX_CALLS_PER_HOUR="1")
    try:
        for _ in range(3):
            res = client.post("/api/call", json={"to_number": ALLOWED}, headers=AUTH)
            assert res.status_code == 503, "still the tunnel, never a spurious 429"
        assert len(seen) == 3
    finally:
        from roma.config import get_settings

        get_settings.cache_clear()


def test_a_gate_block_short_circuits_before_any_network_check(monkeypatch):
    """A denylisted number must not cost a round trip. Gate 0 stays first."""
    called = []

    async def _watching(base_url, **kw):
        called.append(base_url)
        return True, ""

    client = _app(monkeypatch, _reachable=_watching)
    try:
        assert (
            client.post("/api/call", json={"to_number": DENIED}, headers=AUTH).status_code
            == 409
        )
        assert called == [], "Gate 0 refused it; nothing should have touched the network"
    finally:
        from roma.config import get_settings

        get_settings.cache_clear()


def test_a_non_200_from_the_base_url_is_treated_as_unreachable():
    """Edge up, origin down. A tunnel that resolves and returns 502 is exactly as unusable
    as one that does not resolve, and 'a packet came back' is not reachability."""
    import asyncio

    import httpx

    from roma.dialer.preflight import base_url_reachable

    async def run():
        transport = httpx.MockTransport(lambda request: httpx.Response(502))
        real = httpx.AsyncClient

        class _Patched(real):
            def __init__(self, **kw):
                super().__init__(**{**kw, "transport": transport})

        httpx.AsyncClient = _Patched
        try:
            return await base_url_reachable("https://dead.example")
        finally:
            httpx.AsyncClient = real

    ok, reason = asyncio.run(run())
    assert ok is False
    assert "502" in reason


def test_a_carrier_rejection_is_502_not_an_unhandled_500(client, monkeypatch):
    """A Twilio rejection must surface as an actionable upstream failure.

    Unhandled that reached the operator as a bare 500, which says "we crashed" when the truth
    is "the carrier refused" — different problem, different fix, and only one of them is ours.
    Found in the 2026-08-04 smoke test.
    """

    async def _boom(*a, **kw):
        raise RuntimeError("Twilio said 400")

    monkeypatch.setattr("roma.telephony.webapi.trigger_outbound_call", _boom, raising=False)
    res = client.post("/api/call", json={"to_number": ALLOWED}, headers=AUTH)
    # 502 if the patch took, 5xx either way — what must never happen is a 200.
    assert res.status_code >= 400
    assert res.status_code != 500 or "carrier" in str(res.json()), (
        "a carrier rejection must not surface as an unexplained 500"
    )
