# Level 8 — Secure jobs and access

**Roadmap sections:** 26–31. **Status:** Sections 26–28 durable jobs, webhook idempotency and human authentication/RBAC implemented with offline verification; privacy/replay/publication and full gate pending.

## Entry gate

Levels 1/3/7 durable state and reliable lifecycle; required before production traffic.

## Scope when implementation is requested

Add durable job identities, bounded retries/backoff, attempt/reason records, dead-letter retention and authorized replay. Use idempotent effects; distinguish webhook processing lease from completed state and mark completion only after the durable effect.

Implement JWT/session expiry/refresh/revocation, role permissions, signed/HMAC callbacks with stale/replay protection, short-lived stream tokens and shared Redis rate limits. Validate provider signing semantics before wiring them.

Protect PII with appropriate keyed hashing/encryption, recording access audit, approved consent, retention/deletion and backup scope. Browser-bundled bearer values remain local compatibility only.

Reuse existing signed Twilio answer handling and atomic SQL receipt/effect. Twilio signatures do not impose a timestamp freshness window; design replay controls from actual provider support. Reuse Dramatiq and the SQL job ledger, close publication/backpressure/retention gaps, and review the sensitive answer-URL lead query.

## Existing reuse in Voice_Agent

Twilio signature/account checks, atomic answer receipt/call/event, PostgreSQL job ledger, Dramatiq, keyed caller linkage, local bearer guard and consent-gated recording.

## Section 26 background processing

Retain the existing producer/consumer architecture: recording queue/local spool and one recording consumer handle consent-gated file work; `PostcallService` commits the call and unique SQL job intents; the dispatcher publishes UUIDs; Dramatiq workers claim and settle short SQL units. No file conversion, summary, CRM or followup network wait runs in a live-call transaction. `process_recording` intent records the recording consumer's actual outcome; it does not run conversion twice.

Statistics and linked-caller status updates retain their idempotent SQL effects. Future-due jobs stay in PostgreSQL until eligible. Terminal recording failure/missing capture now records `dead_letter`, reviewed reason and attempt count before queue dead-lettering when the durable handoff is available. Business jobs retain five attempts, 60 s execution limit, 300 s lease and capped retry delay; permanent consent/configuration failures terminate visibly rather than spinning. Dead letters are inspectable, not silently deleted or automatically replayed.

`BackgroundHandlers` supports a supplied followup sender only with explicit `consent_confirmed=True`, a stable job idempotency key and positively acknowledged delivery. Default production handoff retains consent=False; no concrete SMS/email/CRM sender is configured and no contact is authorized by scheduling. `update_lead` updates the existing linked local caller, not an external CRM. A sender must honor provider idempotency to avoid repeats after a lost acknowledgement; SQL alone cannot promise exactly-once remote delivery.

Summary UUIDs use `roma_background_summary` / `summarize_background_call`, actor priority 100 versus normal priority 0. SQL dispatch favors normal jobs. This is lower-priority scheduling, not preemption or a measured capacity guarantee; workers remain separate from live-call processes. The default summary is deterministic metadata. Explicit `--allow-paid-summary` selects a separate Claude instance: 128 output tokens, 2,048 reserved tokens and INR0.25 per attempt, with at most five business attempts (planned ceilings 10,240 tokens/INR1.25 per job). Only allowlisted stage/outcome/duration/audio/turn metadata is sent, with no transcript, caller identity or live provider budget shared.

[Message Batches](https://platform.claude.com/docs/en/build-with-claude/batch-processing) is an optional later transport for delayed work; its advertised 50% discount is not used or claimed by the implemented ordinary Messages summary adapter. No batch or paid request was submitted. Offline worker/SQL/Redis tests cover due scheduling, effects, duplicate notifications, permanent/transient failure, exhausted recording handoff, queue routing and paid opt-in refusal. [Verification](../12-verification.md#section-26-background-processing) owns results. Acceptance checkboxes remain pending.

## Section 27 idempotency

Preserve the existing SQL `UNIQUE(provider_event_id)` receipt and call/event effect in one transaction. Shared Redis `RedisWebhookWindow` adds a hashed-identity advisory window: `processing` has a cryptographic owner token and explicit `SET NX EX 60`; only a successful SQL result may atomically replace that owner's marker with `done EX 86400`. Twenty-four-hour completion coverage is longer than the 360-second live-call lease because webhook redeliveries can outlive the call. SQL receipts still survive independently and have no new automatic purge.

An identical active processing lease returns retryable 503 without a second SQL operation. A cached done marker still checks SQL and its digest before returning a duplicate; missing/expired/corrupt or conflicting Redis content never replaces durable authority. A mismatching cache digest also defers to SQL instead of incorrectly rejecting valid durable input. Redis I/O has a 200 ms bound and counted, metadata-only failure logging; SQL acceptance retains its three-second bound. Each advisory operation adds at most its own bound, not an unbounded wait. Provider retry configuration is separate: do not assume any 503 alone enables Twilio redelivery.

Failure/cancellation releases only the matching processing token. A crashed API leaves a short lease which expires; a successor retries the SQL identity. A lost acknowledgement after SQL commit is safe, including a crash before Redis done. Completion/release compares ownership, so late old work cannot delete or finish its successor. SQL independently rejects changed-input identity reuse and protects concurrent delivery even when Redis is unavailable. Scheduled worker effects retain their unique SQL identities and settlement transactions from section 26. Exactly-once business effects do not mean exactly-once delivery or arbitrary external messaging.

Tests exercise pending/done TTLs, expiry/stale ownership, release on failed/cancelled work, cache outages, stale done digests and actual disposable SQL commit-before-done crash recovery. Existing concurrent SQL tests still prove one receipt/call/event. [Verification](../12-verification.md#section-27-webhook-idempotency) owns results. No signed transport/provider retry setting, schema or working database changed; broader callback freshness/privacy/replay acceptance remains pending.

## Section 28 authentication and role permissions

Human access now uses `AuthService`, separate `AuthSettings`, the `/api/v1/auth` router and route-level permissions. Argon2id hashes passwords; creation requires 12–256 characters and at most 1,024 UTF-8 bytes. Unknown-user login performs dummy verification. Native password work has two owned admission slots; cancellation does not release capacity before the hash finishes. No default account, registration endpoint or startup seed exists.

Access tokens use PyJWT with fixed HS256, required typed claims, issuer `roma`, audience `roma-api` and a private signing secret of at least 32 bytes. Default access expiry is 900 seconds; refresh-family lifetime is 604,800 seconds. JWTs contain opaque identifiers, not email/caller data. Every authenticated request checks the current SQL session, user activity and roles. Role changes take effect on the next request; disabling a user revokes families, and reactivation cannot resurrect them. SQL failure denies access with a sanitized unavailable response.

Refresh credentials are random 384-bit opaque values; only SHA-256 hashes are stored. Short row-locked transactions rotate one-use credentials. Concurrent reuse revokes the family, including a successor issued to a racing request. Logout revokes the whole family and therefore its access tokens. Expiry rejects access without relying on row cleanup. Keep consumed refresh hashes until their family can no longer be used; no automatic purge or approved identity-retention policy is implied.

| Operation | Admin | Counsellor | Viewer |
|---|---|---|---|
| View calls / analytics / safe safety-event metadata | Yes | Yes | Yes |
| Read recording references | Yes | Yes | No |
| View/manage appointments and leads | Yes | Yes | No |
| Create call records / dial | Yes | No | No |
| Manage users | Yes | No | No |
| Change safety rules (permission only) | Yes | No | No |
| Upload/approve knowledge (future permission) | Yes | No | No |
| Search knowledge debug API (future permission) | Yes | Yes | No |

Viewer call/list/detail responses redact `recording_url` to null. There is no new recording-download endpoint; expiring object access and access auditing remain privacy work. Safety rules remain code-reviewed, and Level 9 knowledge endpoints are not created by assigning permissions. This implements the proposed baseline with additional Admin-only outbound dialing; institute policy still needs approval before production.

Routes: `POST /api/v1/auth/login`, `POST /api/v1/auth/refresh`, `POST /api/v1/auth/logout`, `GET /api/v1/auth/me`, Admin-only `POST /api/v1/users` and `PATCH /api/v1/users/{user_id}`. Login/refresh responses forbid caching. Login/refresh share Redis-backed HMAC-keyed peer-IP admission: ten attempts per 60 seconds by default, explicit expiry, 429 when exceeded and 503 if the limiter is unavailable. Proxy headers are not trusted. Missing authentication configuration is 503; bad/expired credentials are 401 and insufficient permission is 403.

Existing resources and legacy dial/status routes now require these permissions. `AUTH_ALLOW_LEGACY_API_TOKEN=false` by default; an explicit compatibility opt-in permits the old machine token only for pre-existing resource permissions, never user administration, safety mutation or knowledge management. Invalid/JWT-shaped credentials never downgrade to that token. Signed Twilio `/answer` and `/ws` remain a separate trust boundary.

Migration `20261023_0004` appends two tables to the preserved chain. [Schema rationale](../20-relational-schema.md#section-28-human-session-tables) and [operator setup](../runbook.md#section-28-human-authentication-setup) cover keys, expiry, rollback and explicit bootstrap. This session migrated disposable databases only; configure a private secret, upgrade the intended environment and provision an Admin explicitly before real API use. [Verification](../12-verification.md#section-28-authentication-and-final-integration) records offline evidence. Paid/microphone/native acceptance and all advanced tracks remain deferred; no gate checkbox changes.

## Acceptance gate

- [ ] Expired/revoked/unauthorized identities fail safely; role boundaries and recording access are tested.
- [ ] Forged/stale/duplicate callbacks are harmless under retries/concurrency and partial failure.
- [ ] Crash/retry/replay never duplicates durable effects; queues/dead letters are bounded and inspectable.
- [ ] PII-safe logs and approved retention/deletion/access/consent evidence are present.

## Boundaries and advanced work

No production clearance from an empty denylist, CORS, opaque token URL or assumed consent. No exactly-once delivery claim.

## Evidence

Record revision, reproducible commands, environment/data/configuration identity, actual outcomes, demo/reviewer result and limitations here when this level is implemented. Existing foundations do not automatically pass the full gate. Use the [verification contract](../12-verification.md), [baseline](../roadmaps/level-00-baseline.md) and [decision register](../decisions.md).

Return to the [documentation index](../README.md).

## Section 29 callback and stream admission

Twilio SDK signatures cover the exact configured external URL and all supplied form fields. The answer transport permits only bounded UTF-8 form bodies, rejects duplicate/malformed fields, validates known fields with strict Pydantic schemas, and checks the configured Account SID before durable acceptance. Provider-added fields remain signed inputs; they cannot select a different provider. Security events contain only reviewed metadata/status.

Twilio does not supply a timestamp in its signature. Roma therefore issues an outbound answer intent valid for 300 seconds, bound to its configured account and lead capability. The outbound dial paths include it; expired or forged intents fail before SQL effects. Signed inbound requests retain the provider's signature semantics: old signed inbound requests have no invented freshness guarantee. SQL receipts remain the permanent duplicate-effect authority.

Every live stream additionally needs a signed 120-second capability, delivered through TwiML custom parameters and bound to the account, Call SID and lead. Signature/account/capability validation and shared Redis admission precede model/media construction. Redis admits one stream per call for 600 seconds, exceeding the outbound callback/stream windows; missing Redis denies admission. The bounded start prelude cannot keep unauthenticated resources alive indefinitely. No provider console/retry configuration or live call was changed.

## Section 30 shared request limits

Production composition installs Redis-backed request admission before body parsing and a second budget keyed to the authenticated SQL user (or explicitly enabled machine credential). Keys are HMAC-derived; peer addresses, bearer tokens and caller text are not stored in Redis key names or logs. Client-supplied identity and forwarded-IP headers do not choose a budget.

| Admission category | Default limit |
|---|---|
| Login/refresh, shared peer budget | 10 / 300 seconds |
| Administrative routes, peer budget | 30 / 60 seconds |
| Analytics routes, peer budget | 60 / 60 seconds |
| Provider callbacks including pre-accept /ws, shared peer budget | 120 / 60 seconds |
| AI-test/lab HTTP namespace, peer budget | 5 / 60 seconds |
| Other API requests, peer perimeter | 1,000 / 60 seconds |
| Authenticated user across protected routes | 100 / 60 seconds |

The AI-test namespace is protected if mounted; this section creates no paid/GPU HTTP endpoint and grants no CLI spend permission. The existing auth-service limiter remains an additional configurable bound; its default window is now 300 seconds. Fixed windows use atomic increment/expiry transactions with bounded Redis I/O. Over-limit responses are sanitized 429 with Retry-After; Redis admission failure is 503 and never invokes the protected handler. Health checks remain available. Shared NAT peers share the perimeter budget; proxy trust and production sizing require deployment review.
