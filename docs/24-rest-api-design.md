# Versioned REST API design

**Level 1 section 8.** Roma now exposes versioned JSON resource routes beside the legacy live-call command routes. The v1 API is for durable business resources and dashboard/query workflows. The existing `/api/call` command remains the explicit outbound dial path until a later tested client migration.

## Routes

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/v1/calls` | Create a durable call record without dialing the carrier |
| `GET` | `/api/v1/calls` | List durable call records with pagination, filters and sorting |
| `GET` | `/api/v1/calls/{call_id}` | Read one durable call record |
| `GET` | `/api/v1/leads` | List caller/lead records without exposing phone hashes |
| `POST` | `/api/v1/appointments` | Book an available appointment slot atomically |
| `GET` | `/api/v1/appointments` | List appointments with branch/status filters |
| `PATCH` | `/api/v1/appointments/{appointment_id}` | Update appointment status/counsellor/course fields |
| `DELETE` | `/api/v1/appointments/{appointment_id}` | Cancel an appointment and return the updated record |
| `GET` | `/api/v1/analytics/overview` | Return small operational counts for dashboards |
| `GET` | `/api/v1/safety/events` | List safety events with call/rule filters |

Legacy paths stay available: `/api/call`, `/api/call/{request_uuid}`, `/answer`, `/ws` and `/health`. `POST /api/v1/calls` is deliberately not a hidden phone-dial command; carrier-side effects remain on `/api/call` until the live dial workflow is redesigned around durable call creation.

## Envelope

All v1 JSON resource routes return one of two envelope shapes:

```json
{
  "success": true,
  "data": {},
  "meta": {}
}
```

```json
{
  "success": false,
  "error": {
    "code": "APPOINTMENT_SLOT_UNAVAILABLE",
    "message": "The selected appointment slot is unavailable."
  }
}
```

List routes include `meta.limit`, `meta.offset`, `meta.total`, `meta.sort` and the applied `meta.filters`. Validation failures use `422 VALIDATION_ERROR`. Missing authentication uses `401 UNAUTHORIZED`; configured-but-unavailable durable storage uses `503 DATABASE_UNAVAILABLE`; not-found resources use `404`; conflicting writes use `409`.

## Validation and OpenAPI

Request bodies and query contracts live in `roma/schemas/rest_api.py` and are validated by Pydantic. The FastAPI router in `roma/api/v1/rest.py` adds tags for calls, leads, appointments, analytics and safety so `/openapi.json` documents every v1 route.

Every v1 route uses the same bearer token policy as the legacy call endpoint. This is a Level 1 protection boundary, not the Level 8 RBAC/session model. Roles, sessions, revocation and replay policy remain Level 8.

## Database boundaries

Routes authenticate and parse HTTP, then call `RestApiService`. The service opens short SQLAlchemy units of work or read sessions, performs one resource operation, and closes the session before returning. It never holds a transaction during audio, provider calls or Redis waits. Appointment booking reuses the existing row-locked repository and maps slot conflicts to `APPOINTMENT_SLOT_UNAVAILABLE`.
