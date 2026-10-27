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

List routes include `meta.limit`, `meta.offset`, `meta.total`, `meta.sort` and the applied `meta.filters`. Validation failures use `422 VALIDATION_ERROR`. Missing, malformed or non-ASCII bearer credentials use `401 UNAUTHORIZED`; configured-but-unavailable durable storage, including SQL/native connection-capacity failures, uses `503 DATABASE_UNAVAILABLE`; not-found resources use `404`; conflicting writes use `409`.

## Validation and OpenAPI

Request bodies and query contracts live in `roma/schemas/rest_api.py` and are validated by Pydantic. The FastAPI router in `roma/api/v1/rest.py` adds tags for calls, leads, appointments, analytics and safety so `/openapi.json` documents every v1 route.

Section 28 supersedes the Level 1 shared bearer boundary with JWT/session authentication and per-route permissions for v1 resources and legacy call commands. See [auth routes and roles](levels/level-08.md#section-28-authentication-and-role-permissions). Invalid credentials return 401, insufficient permission 403, unavailable configuration/storage 503 and exhausted auth admission 429. Static-token compatibility defaults off; callback authenticity/replay stays separate.

## Database boundaries

Routes authenticate and parse HTTP, then call `RestApiService`. The service opens short SQLAlchemy units of work or read sessions, performs one resource operation, and closes the session before returning. It never holds a transaction during audio, provider calls or Redis waits. Appointment booking reuses the existing row-locked repository and maps slot conflicts to `APPOINTMENT_SLOT_UNAVAILABLE`.


Section 12 event responses retain `rule`, `original_category`, `replacement_type` and `created_at`, and add `rule_id`, `category`, `replacement_used`, `detected_at` and `latency_ms`. Exposed metadata is allowlisted; arbitrary legacy JSON is not returned. Missing historic measurements remain null. Statistics return `{total, by_category, by_rule}` and count activations after optional call/rule/category filters. Lists and counts exclude soft-deleted events. See [audit semantics](04-guardrails.md#structured-audit-and-storage).

## Appointment holds and confirmation

Section 13 adds protected `GET /api/v1/appointment-slots?branch_id=<uuid>&on=YYYY-MM-DD`, `POST /api/v1/appointment-holds` and `DELETE /api/v1/appointment-holds`. Availability lists at most 100 configured eligible slots in the 14-day window (or selected date), excludes readable existing holds and does not reserve the response. Acquire a hold before offering it; availability alone can race.

Hold POST body: `branch_id`, `appointment_date`, branch-local `start_time`, durable `call_id`, optional UUID `hold_token` for a same-owner retry. Returns 201 with a random `hold_token` and `expires_in_ms`, default TTL 90 seconds. DELETE accepts the same identity with a **required** token in its JSON body; returns `{released: bool}`. Tokens never appear in URLs; ownership comparison prevents stale/foreign cleanup. Release is best effort and TTL guarantees expiry. A token is transient coordination, not an authorization credential or a committed appointment.

Existing appointment POST retains its fields and adds optional UUID `idempotency_key` and `hold_token` (requires `call_id`). Same-key active retries return the same appointment, with 201; changed identity or terminal reuse is 409. Without an idempotency key, independent POSTs retain the existing create/conflict behavior. A readable foreign hold is 409. An expired/lost hold is revalidated using PostgreSQL; Redis outage during final confirmation does not replace SQL authority. New hold/offer acquisition fails with 503 if Redis is unavailable.

Booking uses `BookingService` and `PostgresBookingStore`; commits finish before the success envelope. `APPOINTMENT_SLOT_UNAVAILABLE` is 409; a missing call/appointment (or a slot removed after the availability check) is 404; an unconfigured or ineligible candidate is unavailable (409); `BOOKING_UNAVAILABLE` or `HOLD_UNAVAILABLE` is 503. Validation remains 422, bad/missing bearer 401. No session spans Redis/model/audio waits. Cancellation atomically frees the booked slot; repeating an old cancellation cannot unlock its successor. Terminal appointments cannot be reactivated with PATCH. Section 28 now requires appointments.view/manage permissions; Admin and Counsellor can manage booking, Viewer cannot.

Only active IST branch reference data and configured future slots within existing visiting hours can be booked. Capacity is one active appointment; rescheduling and external calendar/CRM/WhatsApp synchronization are outside section 13. See [booking policy/evidence](levels/level-03.md) and [operations](runbook.md#section-13-booking-operations).

## Governed knowledge resources (sections 32–40)

| Method | Path | Permission and effect |
|---|---|---|
| POST | `/api/v1/knowledge/documents` | Admin `rag.upload`; bounded governed JSON text/Markdown and durable managed ingestion intent |
| POST | `/api/v1/knowledge/documents/{id}/approve` | Admin `rag.approve`; server-recorded actor/time/hash with approval_reference |
| POST | `/api/v1/knowledge/documents/{id}/activate` | Admin `rag.approve`; expected_version plus approved/effective/completed-ingestion fence |
| POST | `/api/v1/knowledge/documents/{id}/archive` | Admin `rag.approve`; expected_version revokes retrieval eligibility |
| POST | `/api/v1/knowledge/documents/{id}/ingest` | Admin `rag.upload`; empty JSON body, idempotent current-content/pipeline enqueue |
| GET | `/api/v1/knowledge/documents/{id}/ingestion-jobs` | Admin `rag.upload`; safe status/attempt/reason/quarantine metadata |
| POST | `/api/v1/knowledge/search` | Admin/Counsellor `rag.debug`; explicitly configured enabled retrieval, metadata-only exact search |

Upload never runs inference or self-approves content. Draft/approved/active/archived versions remain immutable after approval; updated facts require a new version. Processing is the bounded SQL worker drain, with status inspection rather than a fabricated synchronous success. Search accepts query/category/language, optional course/branch/version and explicit English fallback policy; it returns document/chunk IDs, scores, applied filters and retrieval/embedding timings without source text or raw query. Validation/auth/permission, conflict and unavailable errors use the shared v1 envelope with sanitized knowledge codes. Retrieval auditing must succeed before debug output or a grounded fact is released. Counsellor/Viewer have no knowledge mutation/debug grant. Runtime stays off by default; [Level 9](levels/level-09.md) owns its separate quality gate and [runbook](runbook.md#governed-ingestion-retrieval-and-golden-evaluation-sections-3440) owns operation.

## Section 46 protected usage/cost projections

All cost routes require `analytics.view`, use the shared v1 success/error envelope and return `Cache-Control: no-store`:

| Method | Path | Selection |
|---|---|---|
| GET | `/api/v1/analytics/cost/today` | Current UTC day by default; optional `day=YYYY-MM-DD` or `month=YYYY-MM` |
| GET | `/api/v1/analytics/cost/provider` | Same UTC period, optional provider filter and provider groups |
| GET | `/api/v1/analytics/cost/call/{call_id}` | Existing durable call across its lifetime, optional populated turn groups |
| GET | `/api/v1/analytics/cost/average` | Same period and per-currency average among calls with priced usage |

Choose day or month, never both. Reports expose usage/call counts, normalized units, unknown-unit counts, unpriced events, completeness, exclusive UTC period bounds and per-currency Decimal-string amounts. Empty results are incomplete; missing durable call is 404. No exchange conversion or all-currency grand total is returned. Populated turn groups require recorded turn identity; missing historic identity is not invented. Final source/test evidence is [pending](12-verification.md#sections-4146-telephony-serving-telemetry-and-costs--2026-10-26).

`GET /metrics` is a separate private Prometheus text route outside OpenAPI/resource envelopes; actual socket loopback or a live active Admin may scrape. See [observation privacy](07-security.md#sections-4346-service-and-observation-privacy).
