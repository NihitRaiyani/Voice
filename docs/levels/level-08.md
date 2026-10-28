# Level 8 — Secure jobs and access

**Roadmap sections:** 26–31. **Status:** Sections 26–28 durable jobs, webhook idempotency and human authentication/RBAC implemented with offline verification; sections 29–31 callback security, shared admission and managed privacy are implemented; local operational setup is verified after isolated rehearsal; privacy/live acceptance and the full gate remain pending.

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

Routes: `POST /api/v1/auth/login`, `POST /api/v1/auth/refresh`, `POST /api/v1/auth/logout`, `GET /api/v1/auth/me`, Admin-only `POST /api/v1/users` and `PATCH /api/v1/users/{user_id}`. Login/refresh responses forbid caching. Login/refresh share Redis-backed HMAC-keyed peer-IP admission: ten attempts per 300 seconds by default, explicit expiry, 429 when exceeded and 503 if the limiter is unavailable. Proxy headers are not trusted. Missing authentication configuration is 503; bad/expired credentials are 401 and insufficient permission is 403.

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

## Section 31 managed privacy and recording access

Selected recoverable fields, Caller.name and CallTurn.transcript, use authenticated AES-GCM storage when PII_ENCRYPTION_ENABLED is explicitly activated with a separately managed 32-byte hexadecimal key. Associated field context prevents cross-field ciphertext substitution. Legacy plaintext stays readable until the explicit bounded backfill; encrypted rows fail closed without the key. Phone identity continues to use canonical E.164 and the existing keyed HMAC. This does not encrypt every database/Redis field, audio file, checkpoint or backup. Migration 20261024_0005 widens name storage, adds recording disclosure evidence and durable privacy tombstones; no working database, key or legacy row was changed automatically.

Recording capture requires both approved disclosure wording and trusted explicit call-bound consent evidence (grant time, disclosure version and retention expiry). Unknown/legacy evidence does not authorize capture or retention. Worker handoff now writes a real consent/expiry-bearing Recording row with the call/jobs transaction; deleted-call tombstones suppress queued recreation. The pending institute disclosure remains pending, so installation cannot start recording callers.

Recording URL issuance and playback require a current human JWT with recordings.view. Viewer and legacy machine access are denied. Links are signed, user-bound and expire after 120 seconds by default (maximum 300); playback rechecks live authorization, consent, availability, retention and deletion. SQL audit must commit before link or audio output. Both routes prohibit caching and referrer leakage. Owner-only managed files are read through bounded, path/symlink-checked local access; permanent recording URLs are removed from call responses.

Admin-only disposal previews before apply. Both delete/anonymize scrub managed call transcripts, checkpoints, event/job metadata, recording references and linked caller fields, retaining minimized aggregate/audit/idempotency facts and a permanent tombstone. Local artifacts and attributable Redis state/queued jobs are cleaned with explicit retry status; cleanup failures are not reported as completion. Shared caller profiles are disclosed in preview: sibling-call transcripts are not included, so this is call-scoped disposal, not whole-person erasure. Expired stored call/recording/turn records can be selected in bounded batches. Unmapped lead caches, operator exports, providers and backups remain explicit operational work; null legacy expiries are not silently assigned a policy.

Recognized phone/email identifiers are masked in ordinary logs; signed query credentials and private encryption/signing/hash/database secrets are scrubbed. The final JSON logging boundary retains reviewed metadata only. Claude receives minimized recognized identifiers as before; [current provider terms and legal review limits](../07-security.md#claude-api-retention-review--2026-10-24) remain operator gates. No ZDR account entitlement or legal compliance is inferred from these controls.

## Local operational setup — 2026-10-24

[Sanitized operational summary](../../benchmarks/security/local-operations-20261024.json); [integrated verification](../12-verification.md#operational-and-supervised-acceptance-follow-up--2026-10-25).

The owner approved an isolated-first rehearsal followed by local working rollout, and the account Roma Local Admin (`admin@roma.local`). Working `roma` moved from `20261004_0003` / 25 application tables to `20261024_0006` / 31. One approved active Admin exists. Login, refresh rotation/reuse rejection, logout and live-role changes were exercised with the production AuthService and real SQL; smoke sessions were revoked afterward. These are service-level checks, not HTTP startup or deployment acceptance.

Only the approved private JWT, AES field key, recording-link key and field-encryption-enable assignments changed. Keys are independently generated; credentials/key files and encrypted backups are owner-only and excluded from Git. The original PII hash key and unrelated private configuration were preserved. Selected-field backfill preview/apply found zero working plaintext caller names or turn transcripts. Synthetic isolated name/transcript round trips proved encryption/decryption and backfill; this does not encrypt checkpoints, every SQL field, Redis or audio.

Both saved encrypted custom-format pre-upgrade backups were read from disk with the separately stored key, authenticated/decrypted and restored into disposable PostgreSQL. Original revision, counts and row equality were verified. Isolated upgrade/downgrade/reupgrade preserved pre-existing `0003` rows; this is not evidence that downgrades preserve newer auth/knowledge/privacy records. No real working rollback was performed. Keys/backups on one machine are not off-host disaster recovery or independent key escrow.

On 2026-10-25 an independent read-only recheck confirmed head `0006`, the approved Admin, zero active calls, no selected plaintext fields, valid independent key configuration and owner-only permissions. Recording disclosure remains pending and retention unset. [Privacy recommendations](../07-security.md#recommended-policy-before-production-acceptance) and legal/provider/account approval remain separate; no Level 8 gate was marked passed.

### Restore-erasure rehearsal — 2026-10-25

The [synthetic disk restore/disposal drill](../../benchmarks/security/restore-erasure-20261025.json) verified pre-disposal SQL/files/Redis resurrection, separate ledger import, preview/apply re-scrubbing of completed tombstones before traffic, repeated cleanup and queued post-call replay suppression. Working `roma` and supervised voice fixtures were untouched. Unknown exports/providers/replicas, sibling calls and off-host disaster recovery remain outside this proof; runtime retention and disclosure remain unapproved.

### Known-resource provider erasure operator

The separate [preview-first operator](../runbook.md#known-resource-provider-erasure) implements account-bound verification and explicit durable-ledger deletion of known Twilio call, recording and legacy transcription resources. Local disposal retains `external_pending`; no provider operation runs as a deletion/restore side effect. Unknown inventory/archives, Anthropic/Sarvam account-specific retention, backups/exports and whole-person scope still require independent confirmation. This is offline code/tooling evidence, with no real account verification/deletion or privacy gate approval.

<!-- BEGIN CURRENT LIVE ACCEPTANCE -->

## Current live acceptance

Recorded 2026-10-28T14:20:29.183960Z; source commit `40fb273f3a8db1a813456fb48ec0d3f15d1a9fc0`. Run `0b5d2790e1b24e41aa2da4e1bac11041`; status `completed`. Local HMAC receipts establish integrity, not independent acceptance. Governance, provider-account terms and whole-person/backup erasure remain pending independent closure. No recording or retention policy is activated by this receipt.

<!-- END CURRENT LIVE ACCEPTANCE -->


## 2026-10-28 persisted-subject disposal

The Admin privacy API/CLI now supports preview-first disposal over all calls linked to one persisted caller UUID, including UUID-only prior disposal manifests for retries after detachment. A short SQL transaction holds existing SID advisory locks and granular Caller/Call row locks, rechecks the exact graph and rejects active calls before any SQL scrub. Caller foreign-key associations wait behind that row lock while unrelated caller writes remain independent. Lock waits are bounded at three seconds each; no worst-case transaction SLA is claimed for the 1,000-call/history limit.

Selected caller fields and sibling transcripts/checkpoints/events/jobs/recordings are anonymized through the existing contract. Retrieval audits detach whether linked by call ID or turn ID. The anonymized Caller row, appointment statuses and financial totals are retained; this is managed historical-graph disposal, not a claim that all personal copies have been erased. Managed file/Redis cleanup follows the commit and can be retried. Unidentified/unlinked calls, future contact, unknown exports/lead caches/holds, replicas, backups and external providers remain returned scope limits. No working data, vendor resources or policy were changed. See the [review](../decisions.md#2026-10-28-remediation-blueprint-review) and [commands](../runbook.md#remediation-investigation-and-isolated-microphone-speech).
