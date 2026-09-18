from twilio.request_validator import RequestValidator


def external_url(
    public_base_url: str, path: str, query: str = "", *, websocket: bool = False
) -> str:
    base = public_base_url.rstrip("/")
    if websocket:
        base = base.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
    url = f"{base}/{path.lstrip('/')}"
    return f"{url}?{query}" if query else url


def valid_twilio_signature(
    url: str, params: dict[str, str], signature: str, auth_token: str
) -> bool:
    if not signature or not auth_token:
        return False
    return RequestValidator(auth_token).validate(url, params, signature)


__all__ = ["external_url", "valid_twilio_signature"]
