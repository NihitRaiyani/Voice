# 18 — Webhook Idempotency

**Status: Implemented for the existing Twilio `POST /answer` endpoint.**

## The invariant and file flow

One logical answer event creates at most one receipt and one `twilio.answer` event, even
with concurrent delivery, process restart, or a lost HTTP response. A successful durable
acceptance commits its business effect; a rolled-back attempt remains retryable.

```text
Signed POST /answer
  -> API: signature, account, content type, input validation
  -> Twilio adapter: stable event key + business-input digest
  -> service: bounded acceptance (3 seconds)
  -> PostgreSQL transaction
       INSERT receipt ON CONFLICT DO NOTHING
         new -> ensure call exists -> INSERT answer event -> COMMIT
         duplicate -> compare digest -> no second database effect
         error -> ROLLBACK -> HTTP 503
  -> atomic Redis status projection (best effort, safely repeatable)
  -> HTTP 200 + TwiML; X-Webhook-Duplicate: false / true
```

| File | Responsibility |
|---|---|
| `roma/api/v1/twilio_webhooks.py` | Authenticate, validate, invoke service, map 200/400/403/409/415/503 |
| `roma/providers/telephony/twilio/webhooks.py` | Normalize Twilio inputs; derive stable identity and digest |
| `roma/domain/webhooks.py` | Event data, repository protocol, domain errors |
| `roma/services/webhook_service.py` | Bound acceptance time; timeout becomes retryable failure |
| `roma/repositories/postgres/webhooks.py` | Atomic receipt + call + event, duplicate/conflict handling |
| `roma/repositories/postgres/models/webhooks.py` | Receipt model and unique provider-event identity |
| `migrations/versions/20260930_0002_webhook_receipts.py` | Add receipt table without rewriting the original migration |
| `roma/repositories/redis/call_status.py` | Atomic, monotonic status updates using WATCH/MULTI |
| `roma/main.py` | Wire the database/service and dispose its pool on shutdown |
| `scripts/serve_media.py` | Reuse production wiring for the debug server |
| `roma/repositories/postgres/background_jobs.py` | Finalize the existing call, including its actual start time |

The key is `twilio:<AccountSid>:<CallSid>:answer`. It identifies an answer operation, not
every possible event for a call. Twilio documents `CallSid` as a call identifier and its
`I-Twilio-Idempotency-Token` header in the retry-attempt context. We deliberately derive a
business identity rather than trusting an arbitrary header as a durable event identity.
Sources: [Twilio request parameters](https://www.twilio.com/docs/voice/twiml) and
[webhook retries](https://www.twilio.com/docs/usage/webhooks/webhooks-connection-overrides).

## Failure behavior

| Scenario | Result |
|---|---|
| First valid delivery | One receipt, call, and answer event committed; HTTP 200 |
| Duplicate or 20 simultaneous deliveries | One winner; others observe the committed receipt; HTTP 200 |
| Process crashes before commit | Transaction rolls back; retry can win |
| Commit succeeds but HTTP response is lost | Retry sees receipt and returns TwiML without a second event |
| Same identity, changed lead/direction | HTTP 409; original effect remains unchanged |
| Wrong signature/account | HTTP 403 before persistence |
| Missing/malformed Call SID | HTTP 400 before persistence |
| Database absent, failed, or timed out | HTTP 503; configure provider retries for this failure |
| Redis fails after database commit | Durable effect survives; projection may stay stale |
| Duplicate answer arrives after call ended | Redis preserves `ended`, outcome, and TTL |
| Call/event deleted, old delivery arrives | Retained receipt suppresses recreation |

Redis is not transactionally coupled to PostgreSQL. Replaying its idempotent projection
on every accepted delivery repairs a missed projection when redelivery occurs. This phase
does not introduce automatic Redis reconciliation or external SMS/provider effects.

Receipts contain a namespaced identifier, digest, UUID, and creation time. No raw provider
body, phone number, transcript, or lead token is stored. They have no automatic expiry;
purging receipts reduces the deduplication guarantee to the retained period. A signed old
request is deduplicated, not rejected using a timestamp-based replay window.

## Practice and inspect real rows

Apply the migration before restarting your local backend. `DATABASE_URL` must be configured.

```bash
uv run --extra dev alembic upgrade head
```

Run focused tests (PostgreSQL tests use a disposable database; no paid APIs):

```bash
uv run --extra telephony --extra dev pytest -q \
  tests/api/v1/test_webhook_idempotency.py \
  tests/repositories/postgres/test_webhooks.py \
  tests/repositories/redis/test_call_status.py
```

For visible rows in your development database, start the backend on port 8020 and run
this from the repository. It submits the same synthetic signed HTTP request twice; it
does not contact Twilio, dial a number, or open an audio WebSocket. The first print should
be `200 false`, the second `200 true`. A new script run uses a different synthetic Call SID.

```python
from uuid import uuid4

import httpx
from twilio.request_validator import RequestValidator
from roma.core.config import get_settings

settings = get_settings()
call_sid = "CA" + uuid4().hex
params = {
    "AccountSid": settings.twilio_account_sid.get_secret_value(),
    "CallSid": call_sid,
    "Direction": "inbound",
}
public_url = settings.public_base_url.rstrip("/") + "/answer"
signature = RequestValidator(
    settings.twilio_auth_token.get_secret_value()
).compute_signature(public_url, params)
with httpx.Client() as client:
    for _ in range(2):
        response = client.post(
            "http://127.0.0.1:8020/answer",
            data=params,
            headers={"X-Twilio-Signature": signature},
        )
        print(response.status_code, response.headers.get("X-Webhook-Duplicate"))
print("Synthetic call:", call_sid)
```

In pgAdmin/psql connected to the same database as `DATABASE_URL`, inspect:

```sql
SELECT r.provider_event_id, r.created_at, c.provider_call_id,
       c.status, e.event_type
FROM webhook_receipts r
JOIN call_events e ON e.idempotency_key = r.provider_event_id
JOIN calls c ON c.id = e.call_id
ORDER BY r.created_at DESC
LIMIT 20;

SELECT idempotency_key, count(*)
FROM call_events
WHERE event_type = 'twilio.answer'
GROUP BY idempotency_key
HAVING count(*) > 1;
-- Must return zero rows.
```

The synthetic call remains `in_progress`: this exercise sends only an answer webhook,
not a real call and post-call message. Retry/concurrency/rollback exercises belong in
the disposable tests; do not inject deliberate database failures into your working database.

## Interview practice

1. **Two processes both receive the webhook. Why does a pre-insert SELECT fail?**
   Both can observe absence. The unique index arbitrates the INSERT; PostgreSQL waits
   for the competing transaction, then suppresses its duplicate if that transaction commits.

2. **Why must the receipt and event share a transaction?**
   A separately committed receipt could survive a crash before the event write, causing
   retries to skip an effect that never happened. One transaction commits both or neither.

3. **What if COMMIT succeeds but its acknowledgement is lost?**
   The caller cannot infer failure from a timeout. It retries the same identity; a committed
   receipt prevents another effect. This handles an ambiguous outcome safely.

4. **Why not just SET NX in Redis before inserting the event?**
   Redis and PostgreSQL do not share that transaction. A crash leaves a claim without an
   effect; expiry or eviction can allow repeats. Redis can assist, but SQL owns this invariant.

5. **Why is CallSid alone insufficient for all callbacks?**
   Answered, completed, and recording events can share a call. Include event kind and provider
   scope; future callbacks with multiple events of one kind need their own stable event IDs.

6. **Does this deliver exactly-once execution?**
   HTTP handlers can execute repeatedly. We guarantee one committed database effect per retained
   event identity. Remote APIs need their own idempotency contracts or an outbox workflow.

7. **Why return TwiML again instead of an empty 200?**
   Twilio still needs valid call instructions when an earlier response was lost. Duplicate
   business effects are suppressed while the provider response remains usable.

8. **Why compare a digest on duplicates?**
   Reusing an identity with changed business input is not a harmless retry. We reject it with
   409. Hash normalized relevant input, excluding delivery metadata that legitimately changes.

9. **What race did the Redis fix address?**
   An answer could read an earlier status while teardown wrote `ended`, then overwrite it.
   WATCH detects intervening writes; the retry re-reads and preserves the terminal state.

10. **How does retention affect correctness?**
    Deleting a receipt allows that identity to be processed again. Keeping minimal receipts
    independently of call rows prevents old retries from recreating deleted business data.

11. **What did the handoff test expose?**
    The worker previously inserted new calls after teardown. Now a call exists at answer time,
    so its upsert must also update the actual call start time while preserving the answer time.

12. **Why test on PostgreSQL instead of mocking a dictionary?**
    A dictionary cannot prove unique-index contention, transaction rollback, isolation, or
    migration correctness. Unit tests check HTTP policy; real database tests prove atomicity.
