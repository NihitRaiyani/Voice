from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from roma.api.v1.privacy import router
from roma.api.v1.responses import ApiError, api_error_handler
from roma.services.auth_service import AuthError, Principal


def test_recordings_and_disposal_require_human_jwt_and_live_roles():
    app = FastAPI()
    app.include_router(router)
    app.add_exception_handler(ApiError, api_error_handler)
    auth = AsyncMock()
    app.state.auth_service = auth
    app.state.recording_service = AsyncMock()
    app.state.privacy_service = AsyncMock()
    app.state.recording_service.issue.return_value = {
        "url": "/synthetic",
        "expires_at": "later",
    }
    app.state.recording_service.play.return_value = (b"RIFFtest", "audio/wav")
    app.state.privacy_service.dispose.return_value = {"applied": False}
    url = f"/api/v1/recordings/{uuid4()}/access"
    headers = {"Authorization": "Bearer synthetic.jwt"}
    with TestClient(app) as client:
        assert client.post(url).status_code == 401
        auth.authenticate.side_effect = AuthError()
        assert client.post(url, headers={"Authorization": "Bearer machine"}).status_code == 401
        auth.authenticate.side_effect = None
        auth.authenticate.return_value = Principal(
            uuid4(), "viewer@example.invalid", "Viewer", frozenset({"viewer"})
        )
        assert client.post(url, headers=headers).status_code == 403
        auth.authenticate.return_value = Principal(
            uuid4(), "staff@example.invalid", "Staff", frozenset({"counsellor"})
        )
        response = client.post(url, headers=headers)
        assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
        assert (
            client.post(
                f"/api/v1/privacy/calls/{uuid4()}", json={}, headers=headers
            ).status_code
            == 403
        )
        auth.authenticate.return_value = Principal(
            uuid4(), "admin@example.invalid", "Admin", frozenset({"admin"})
        )
        assert (
            client.post(
                f"/api/v1/privacy/calls/{uuid4()}", json={}, headers=headers
            ).status_code
            == 200
        )
        assert app.state.privacy_service.dispose.call_args.kwargs["apply"] is False
        auth.authenticate.side_effect = [auth.authenticate.return_value, AuthError()]
        content = f"/api/v1/recordings/{uuid4()}/content?token=synthetic"
        assert client.get(content, headers=headers).status_code == 401
        actor = Principal(uuid4(), "staff@example.invalid", "Staff", frozenset({"counsellor"}))
        downgraded = Principal(
            actor.user_id, actor.email, actor.display_name, frozenset({"viewer"})
        )
        auth.authenticate.side_effect = [actor, downgraded]
        response = client.get(content, headers=headers)
        assert response.status_code == 403 and b"RIFFtest" not in response.content
        auth.authenticate.side_effect = [actor, downgraded]
        assert client.post(url, headers=headers).status_code == 403


def test_subject_disposal_requires_admin_is_preview_first_and_maps_blocked_subjects():
    app = FastAPI()
    app.include_router(router)
    app.add_exception_handler(ApiError, api_error_handler)
    auth = AsyncMock()
    app.state.auth_service = auth
    app.state.privacy_service = AsyncMock()
    app.state.privacy_service.dispose_subject.return_value = {"applied": False}
    caller_id = uuid4()
    url = f"/api/v1/privacy/subjects/{caller_id}"
    headers = {"Authorization": "Bearer synthetic.jwt"}
    with TestClient(app) as client:
        assert client.post(url, json={}).status_code == 401
        auth.authenticate.return_value = Principal(
            uuid4(), "staff@example.invalid", "Staff", frozenset({"counsellor"})
        )
        assert client.post(url, json={}, headers=headers).status_code == 403
        admin = Principal(uuid4(), "admin@example.invalid", "Admin", frozenset({"admin"}))
        auth.authenticate.return_value = admin
        response = client.post(url, json={}, headers=headers)
        assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
        app.state.privacy_service.dispose_subject.assert_awaited_once_with(
            caller_id, action="delete", apply=False, actor_id=admin.user_id
        )
        assert client.post(url, json={"apply": "true"}, headers=headers).status_code == 422
        app.state.privacy_service.dispose_subject.side_effect = ValueError("private detail")
        response = client.post(url, json={"apply": True}, headers=headers)
        assert response.status_code == 409 and "private detail" not in response.text
        app.state.privacy_service.dispose_subject.side_effect = LookupError("private detail")
        assert client.post(url, json={}, headers=headers).status_code == 404
