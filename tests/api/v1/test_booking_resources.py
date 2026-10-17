from datetime import datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from roma.api.v1.calls import mount_web_api
from roma.api.v1.rest import _service_from_request
from roma.core.config import get_settings
from roma.domain.appointments.timeresolve import IST
from roma.services.booking_service import BookingService
from roma.services.rest_api_service import RestApiService


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("API_TOKEN", "booking-token")
    get_settings.cache_clear()
    store, holds = AsyncMock(), AsyncMock()
    store.available.return_value = True
    store.call.return_value = object()
    store.candidates.return_value = []
    holds.acquire.return_value = 90000
    holds.release.return_value = False
    now = datetime(2026, 10, 17, 9, tzinfo=IST)
    service = RestApiService(AsyncMock(), BookingService(store, holds, now=lambda: now))
    app = FastAPI()
    mount_web_api(app)
    app.dependency_overrides[_service_from_request] = lambda: service
    with TestClient(app) as client:
        yield client, store, holds
    get_settings.cache_clear()


def test_hold_endpoints_validation_ownership_and_error_envelope(client):
    client, _store, holds = client
    auth = {"Authorization": "Bearer booking-token"}
    payload = {
        "branch_id": str(uuid4()),
        "appointment_date": "2026-10-18",
        "start_time": "11:00:00",
        "call_id": str(uuid4()),
    }
    assert client.post("/api/v1/appointment-holds", json=payload).status_code == 401
    held = client.post("/api/v1/appointment-holds", json=payload, headers=auth)
    assert held.status_code == 201
    data = held.json()["data"]
    assert data["expires_in_ms"] == 90000 and data["hold_token"]
    assert (
        client.get(
            f"/api/v1/appointment-slots?branch_id={payload['branch_id']}", headers=auth
        ).json()["data"]
        == []
    )
    assert (
        client.request(
            "DELETE", "/api/v1/appointment-holds", json=payload, headers=auth
        ).status_code
        == 422
    )
    released = client.request(
        "DELETE",
        "/api/v1/appointment-holds",
        json={**payload, "hold_token": data["hold_token"]},
        headers=auth,
    )
    assert released.status_code == 200 and released.json()["data"]["released"] is False
    holds.acquire.return_value = 0
    conflict = client.post("/api/v1/appointment-holds", json=payload, headers=auth)
    assert (
        conflict.status_code == 409
        and conflict.json()["error"]["code"] == "APPOINTMENT_SLOT_UNAVAILABLE"
    )
    from redis.exceptions import ConnectionError as RedisConnectionError

    holds.acquire.side_effect = RedisConnectionError("down")
    failure = client.post("/api/v1/appointment-holds", json=payload, headers=auth)
    assert failure.status_code == 503 and failure.json()["error"]["code"] == "HOLD_UNAVAILABLE"
