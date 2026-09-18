import pytest
from twilio.request_validator import RequestValidator

from roma.telephony.twilio_auth import external_url, valid_twilio_signature


def test_external_url_joins_the_public_origin_path_and_query():
    assert (
        external_url("https://voice.example", "/answer", "lead=abc")
        == "https://voice.example/answer?lead=abc"
    )


@pytest.mark.parametrize(
    ("public_base_url", "expected"),
    [
        ("https://voice.example/", "wss://voice.example/ws"),
        ("http://voice.example/", "ws://voice.example/ws"),
    ],
)
def test_external_url_converts_http_origins_for_websockets(public_base_url, expected):
    assert external_url(public_base_url, "/ws", websocket=True) == expected


def test_valid_twilio_signature_accepts_an_authentic_request():
    auth_token = "test-auth-token"
    url = "https://voice.example/answer"
    params = {"CallSid": "CA123", "From": "+15551234567"}
    signature = RequestValidator(auth_token).compute_signature(url, params)

    assert valid_twilio_signature(url, params, signature, auth_token) is True


def test_valid_twilio_signature_rejects_tampering_and_missing_credentials():
    auth_token = "test-auth-token"
    url = "https://voice.example/answer"
    params = {"CallSid": "CA123"}
    signature = RequestValidator(auth_token).compute_signature(url, params)

    assert valid_twilio_signature(url, {"CallSid": "tampered"}, signature, auth_token) is False
    assert valid_twilio_signature(url, params, "", auth_token) is False
    assert valid_twilio_signature(url, params, signature, "") is False
