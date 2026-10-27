from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from roma.api.v1.responses import ApiError, api_error_handler
from roma.api.v1.supervisor import router
from roma.services.auth_service import Principal
from starlette.websockets import WebSocketDisconnect


def app(role="admin"):
    api = FastAPI()
    api.include_router(router)
    api.add_exception_handler(ApiError, api_error_handler)
    api.state.request_rate_limiter = type(
        "Limiter", (), {"take": AsyncMock(return_value=True)}
    )()
    api.state.auth_service = type(
        "Auth",
        (),
        {
            "authenticate": AsyncMock(
                return_value=Principal(
                    uuid4(), "synthetic@example.invalid", "Synthetic", frozenset({role})
                )
            )
        },
    )()
    redis = type(
        "Redis", (), {"set": AsyncMock(return_value=True), "eval": AsyncMock(return_value=1)}
    )()
    api.state.supervisor_hub = type("Hub", (), {"redis": redis})()
    return api


@pytest.mark.parametrize(
    "path,headers",
    [
        ("/api/v1/supervisor/ws?token=secret", {}),
        ("/api/v1/supervisor/ws", {"origin": "https://evil.invalid"}),
    ],
)
def test_supervisor_denies_url_credentials_and_cross_origin(path, headers):
    with TestClient(app()) as client, pytest.raises(WebSocketDisconnect) as error:
        with client.websocket_connect(path, headers=headers):
            pass
    assert error.value.code == 1008


def test_supervisor_websocket_denies_counsellor_and_rejects_unknown_admission_keys():
    for role, payload in [
        ("counsellor", {"token": "fixture"}),
        ("admin", {"token": "fixture", "phone": "private"}),
    ]:
        with TestClient(app(role)) as client:
            with client.websocket_connect("/api/v1/supervisor/ws") as websocket:
                websocket.send_json(payload)
                with pytest.raises(WebSocketDisconnect) as error:
                    websocket.receive_json()
                assert error.value.code == 1008


def test_supervisor_lease_is_owned_and_released_on_disconnect(monkeypatch):
    import roma.api.v1.supervisor as routes

    async def relay(websocket, redis, authorize):
        assert await authorize()
        await websocket.send_json({"kind": "resync_required"})
        await websocket.receive_text()

    monkeypatch.setattr(routes, "relay_supervisor", relay)
    api = app()
    with TestClient(api) as client:
        with client.websocket_connect("/api/v1/supervisor/ws") as websocket:
            websocket.send_json({"token": "fixture"})
            assert websocket.receive_json()["kind"] == "resync_required"
    assert api.state.supervisor_hub.redis.set.await_count == 1
    assert api.state.supervisor_hub.redis.eval.await_count == 2
