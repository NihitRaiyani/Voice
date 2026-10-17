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
| `GET` | `/api/v1/safety/events` | List safety events with call/rule/category filters |
| `GET` | `/api/v1/safety/stats` | Section 12 activation counts grouped by category and rule |
| `GET` | `/api/v1/calls/{call_id}/safety-events` | Section 12 paginated audit for an existing call; unknown call is 404 |

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


Section 12 event responses retain `rule`, `original_category`, `replacement_type` and `created_at`, and add `rule_id`, `category`, `replacement_used`, `detected_at` and `latency_ms`. Exposed metadata is allowlisted; arbitrary legacy JSON is not returned. Missing historic measurements remain null. Statistics return `{total, by_category, by_rule}` and count activations after optional call/rule/category filters. Lists and counts exclude soft-deleted events. See [audit semantics](04-guardrails.md#structured-audit-and-storage).

## Appointment holds and confirmation

Section 13 adds protected `GET /api/v1/appointment-slots?branch_id=<uuid>&on=YYYY-MM-DD`, `POST /api/v1/appointment-holds` and `DELETE /api/v1/appointment-holds`. Availability lists at most 100 configured eligible slots in the 14-day window (or selected date), excludes readable existing holds and does not reserve the response. Acquire a hold before offering it; availability alone can race.

Hold POST body: `branch_id`, `appointment_date`, branch-local `start_time`, durable `call_id`, optional UUID `hold_token` for a same-owner retry. Returns 201 with a random `hold_token` and `expires_in_ms`, default TTL 90 seconds. DELETE accepts the same identity with a **required** token in its JSON body; returns `{released: bool}`. Tokens never appear in URLs; ownership comparison prevents stale/foreign cleanup. Release is best effort and TTL guarantees expiry. A token is transient coordination, not an authorization credential or a committed appointment.

Existing appointment POST retains its fields and adds optional UUID `idempotency_key` and `hold_token` (requires `call_id`). Same-key active retries return the same appointment, with 201; changed identity or terminal reuse is 409. Without an idempotency key, independent POSTs retain the existing create/conflict behavior. A readable foreign hold is 409. An expired/lost hold is revalidated using PostgreSQL; Redis outage during final confirmation does not replace SQL authority. New hold/offer acquisition fails with 503 if Redis is unavailable.

Booking uses `BookingService` and `PostgresBookingStore`; commits finish before the success envelope. `APPOINTMENT_SLOT_UNAVAILABLE` is 409; a missing call/appointment (or a slot removed after the availability check) is 404; an unconfigured or ineligible candidate is unavailable (409); `BOOKING_UNAVAILABLE` or `HOLD_UNAVAILABLE` is 503. Validation remains 422, bad/missing bearer 401. No session spans Redis/model/audio waits. Cancellation atomically frees the booked slot; repeating an old cancellation cannot unlock its successor. Terminal appointments cannot be reactivated with PATCH. The current shared bearer remains the security boundary; Level 8 RBAC is pending.

Only active IST branch reference data and configured future slots within existing visiting hours can be booked. Capacity is one active appointment; rescheduling and external calendar/CRM/WhatsApp synchronization are outside section 13. See [booking policy/evidence](levels/level-03.md) and [operations](runbook.md#section-13-booking-operations).
