# Level 8 — Secure jobs and access

**v4 sections:** 26–31. **Status:** Signed answer and durable jobs implemented; identity/privacy/replay/publication gate pending.

## Entry gate

Levels 1/3/7 durable state and reliable lifecycle; required before production traffic.

## Scope when implementation is requested

Add durable job identities, bounded retries/backoff, attempt/reason records, dead-letter retention and authorized replay. Use idempotent effects; distinguish webhook processing lease from completed state and mark completion only after the durable effect.

Implement JWT/session expiry/refresh/revocation, role permissions, signed/HMAC callbacks with stale/replay protection, short-lived stream tokens and shared Redis rate limits. Validate provider signing semantics before wiring them.

Protect PII with appropriate keyed hashing/encryption, recording access audit, approved consent, retention/deletion and backup scope. Browser-bundled bearer values remain local compatibility only.

Reuse existing signed Twilio answer handling and atomic SQL receipt/effect. Twilio signatures do not impose a timestamp freshness window; design replay controls from actual provider support. Reuse Dramatiq and the SQL job ledger, close publication/backpressure/retention gaps, and review the sensitive answer-URL lead query.

## Existing reuse in Voice_Agent

Twilio signature/account checks, atomic answer receipt/call/event, PostgreSQL job ledger, Dramatiq, keyed caller linkage, local bearer guard and consent-gated recording.

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
