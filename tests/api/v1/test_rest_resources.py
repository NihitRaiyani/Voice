from __future__ import annotations

from datetime import date
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from roma.api.v1.calls import mount_web_api
from roma.api.v1.rest import _service_from_request
from roma.services.rest_api_service import RestApiServiceError

TOKEN = "level-8-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
CALL_ID = uuid4()
APPOINTMENT_ID = uuid4()
BRANCH_ID = uuid4()
CALLER_ID = uuid4()
SAFETY_ID = uuid4()


class FakeRestApiService:
    def __init__(self) -> None:
        self.booked = False

    async def create_call(self, payload):
        return {
            "id": str(CALL_ID),
            "caller_id": None,
            "provider_call_id": payload.provider_call_id,
            "direction": payload.direction,
            "status": payload.status,
            "language": payload.language,
            "started_at": "2026-10-04T09:00:00+00:00",
            "answered_at": None,
            "ended_at": None,
            "final_stage": None,
            "booking_status": None,
            "total_cost": None,
            "currency": None,
            "recording_url": None,
            "created_at": "2026-10-04T09:00:00+00:00",
            "updated_at": "2026-10-04T09:00:00+00:00",
        }

    async def list_calls(self, query):
        return [
            {
                "id": str(CALL_ID),
                "caller_id": None,
                "provider_call_id": None,
                "direction": "outbound",
                "status": query.status,
                "language": query.language,
                "started_at": "2026-10-04T09:00:00+00:00",
                "answered_at": None,
                "ended_at": None,
                "final_stage": None,
                "booking_status": None,
                "total_cost": None,
                "currency": None,
                "recording_url": None,
                "created_at": "2026-10-04T09:00:00+00:00",
                "updated_at": "2026-10-04T09:00:00+00:00",
            }
        ], {
            "limit": query.limit,
            "offset": query.offset,
            "total": 1,
            "sort": query.sort,
            "filters": {"status": query.status, "language": query.language},
        }

    async def get_call(self, call_id: UUID):
        if call_id != CALL_ID:
            raise RestApiServiceError(404, "CALL_NOT_FOUND", "Call was not found.")
        return {"id": str(CALL_ID), "status": "pending"}

    async def list_leads(self, query):
        return [
            {
                "id": str(CALLER_ID),
                "name": "Roma Lead",
                "preferred_language": query.preferred_language,
                "city": query.city,
                "education": "12th",
                "current_status": "interested",
                "created_at": "2026-10-04T09:00:00+00:00",
                "updated_at": "2026-10-04T09:00:00+00:00",
            }
        ], {
            "limit": query.limit,
            "offset": query.offset,
            "total": 1,
            "sort": query.sort,
            "filters": {"city": query.city, "preferred_language": query.preferred_language},
        }

    async def create_appointment(self, payload):
        if self.booked:
            raise RestApiServiceError(
                409,
                "APPOINTMENT_SLOT_UNAVAILABLE",
                "The selected appointment slot is unavailable.",
            )
        self.booked = True
        return _appointment_data(payload.status)

    async def list_appointments(self, query):
        return [_appointment_data("confirmed")], {
            "limit": query.limit,
            "offset": query.offset,
            "total": 1,
            "sort": query.sort,
            "filters": {"branch_id": str(query.branch_id)},
        }

    async def update_appointment(self, appointment_id: UUID, payload):
        assert appointment_id == APPOINTMENT_ID
        return _appointment_data(payload.status)

    async def cancel_appointment(self, appointment_id: UUID):
        assert appointment_id == APPOINTMENT_ID
        return _appointment_data("cancelled")

    async def analytics_overview(self):
        return {"calls": {"total": 1, "completed": 0}, "safety": {"events": 1}}

    async def list_safety_events(self, query):
        return [
            {
                "id": str(SAFETY_ID),
                "call_id": None,
                "turn_id": None,
                "rule": query.rule,
                "original_category": "fee_claim",
                "replacement_type": "safe_substitution",
                "metadata": {"source": "test"},
                "created_at": "2026-10-04T09:01:00+00:00",
            }
        ], {
            "limit": query.limit,
            "offset": query.offset,
            "total": 1,
            "sort": query.sort,
            "filters": {"rule": query.rule},
        }


def _appointment_data(status: str) -> dict[str, object | None]:
    return {
        "id": str(APPOINTMENT_ID),
        "caller_id": str(CALLER_ID),
        "call_id": None,
        "branch_id": str(BRANCH_ID),
        "appointment_date": date(2026, 10, 5).isoformat(),
        "start_time": "10:00:00",
        "counsellor_id": None,
        "course_id": None,
        "status": status,
        "created_at": "2026-10-04T09:00:00+00:00",
        "updated_at": "2026-10-04T09:00:00+00:00",
    }


@pytest.fixture()
def client(monkeypatch):
    from roma.core.config import Settings, get_settings

    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("SARVAM_API_KEY", "sarvam-test")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-test")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("API_TOKEN", TOKEN)
    get_settings.cache_clear()
    app = FastAPI()
    mount_web_api(app)
    service = FakeRestApiService()
    app.dependency_overrides[_service_from_request] = lambda: service
    yield TestClient(app)
    get_settings.cache_clear()


def test_v1_calls_use_envelopes_pagination_filtering_and_lookup(client):
    created = client.post(
        "/api/v1/calls",
        json={"direction": "outbound", "status": "pending", "language": "hi-IN"},
        headers=AUTH,
    )
    assert created.status_code == 201
    body = created.json()
    assert body["success"] is True
    assert body["data"]["id"] == str(CALL_ID)
    assert body["data"]["status"] == "pending"
    assert body["meta"] == {}

    listed = client.get(
        "/api/v1/calls?status=pending&language=hi-IN&limit=1&offset=0",
        headers=AUTH,
    )
    assert listed.status_code == 200
    body = listed.json()
    assert body["success"] is True
    assert body["data"][0]["id"] == str(CALL_ID)
    assert body["meta"] == {
        "limit": 1,
        "offset": 0,
        "total": 1,
        "sort": "created_at_desc",
        "filters": {"status": "pending", "language": "hi-IN"},
    }

    detail = client.get(f"/api/v1/calls/{CALL_ID}", headers=AUTH)
    assert detail.status_code == 200
    assert detail.json()["data"]["id"] == str(CALL_ID)


def test_v1_auth_validation_and_database_errors_are_enveloped(client, monkeypatch):
    unauthorized = client.get("/api/v1/calls")
    assert unauthorized.status_code == 401
    assert unauthorized.json() == {
        "success": False,
        "error": {"code": "UNAUTHORIZED", "message": "Bad or missing bearer token."},
    }

    validation = client.get("/api/v1/calls?limit=0", headers=AUTH)
    assert validation.status_code == 422
    assert validation.json()["error"]["code"] == "VALIDATION_ERROR"

    app = FastAPI()
    mount_web_api(app)
    unavailable = TestClient(app).get("/api/v1/calls", headers=AUTH)
    assert unavailable.status_code == 503
    assert unavailable.json()["error"]["code"] == "DATABASE_UNAVAILABLE"


def test_v1_leads_safety_and_analytics_read_resources(client):
    leads = client.get("/api/v1/leads?city=Ahmedabad&preferred_language=hi-IN", headers=AUTH)
    assert leads.status_code == 200
    assert leads.json()["data"][0]["id"] == str(CALLER_ID)
    assert leads.json()["meta"]["filters"] == {
        "city": "Ahmedabad",
        "preferred_language": "hi-IN",
    }

    safety = client.get("/api/v1/safety/events?rule=money_guardrail", headers=AUTH)
    assert safety.status_code == 200
    assert safety.json()["data"][0]["id"] == str(SAFETY_ID)
    assert safety.json()["data"][0]["metadata"] == {"source": "test"}

    overview = client.get("/api/v1/analytics/overview", headers=AUTH)
    assert overview.status_code == 200
    assert overview.json()["data"]["safety"] == {"events": 1}


def test_v1_appointments_create_update_delete_and_conflict(client):
    payload = {
        "branch_id": str(BRANCH_ID),
        "caller_id": str(CALLER_ID),
        "appointment_date": "2026-10-05",
        "start_time": "10:00:00",
        "status": "booked",
    }

    created = client.post("/api/v1/appointments", json=payload, headers=AUTH)
    assert created.status_code == 201
    assert created.json()["data"]["status"] == "booked"

    conflict = client.post("/api/v1/appointments", json=payload, headers=AUTH)
    assert conflict.status_code == 409
    assert conflict.json()["error"] == {
        "code": "APPOINTMENT_SLOT_UNAVAILABLE",
        "message": "The selected appointment slot is unavailable.",
    }

    patched = client.patch(
        f"/api/v1/appointments/{APPOINTMENT_ID}",
        json={"status": "confirmed"},
        headers=AUTH,
    )
    assert patched.status_code == 200
    assert patched.json()["data"]["status"] == "confirmed"

    listed = client.get(f"/api/v1/appointments?branch_id={BRANCH_ID}", headers=AUTH)
    assert listed.status_code == 200
    assert listed.json()["meta"]["filters"] == {"branch_id": str(BRANCH_ID)}

    deleted = client.delete(f"/api/v1/appointments/{APPOINTMENT_ID}", headers=AUTH)
    assert deleted.status_code == 200
    assert deleted.json()["data"]["status"] == "cancelled"


def test_openapi_documents_the_versioned_resources(client):
    paths = client.get("/openapi.json").json()["paths"]
    for path in [
        "/api/v1/calls",
        "/api/v1/calls/{call_id}",
        "/api/v1/leads",
        "/api/v1/appointments",
        "/api/v1/appointments/{appointment_id}",
        "/api/v1/analytics/overview",
        "/api/v1/safety/events",
    ]:
        assert path in paths
