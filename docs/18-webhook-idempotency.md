# Answer-webhook idempotency

**Present implementation; reused at v4 Level 8.** One logical Twilio answer commits one durable receipt/call/event even when handlers execute repeatedly. This is a database-effect guarantee while receipts are retained, not exactly-once remote execution.

## Flow and source

Signed form POST `/answer` → validate signature/account/input → normalize logical identity/digest → three-second service bound → one PostgreSQL transaction → repeatable Redis status projection → valid TwiML.

| Source | Responsibility |
|---|---|
| `roma/api/v1/twilio_webhooks.py` | Form/signature/account validation and 200/400/403/409/415/503 mapping |
| `roma/providers/telephony/twilio/webhooks.py` | `twilio:<AccountSid>:<CallSid>:answer` key and business-input digest |
| `roma/domain/webhooks.py`, `roma/services/webhook_service.py` | Contracts/errors and bounded acceptance |
| `roma/repositories/postgres/webhooks.py` | Unique receipt insert and atomic call/event effect |
| `roma/repositories/postgres/models/webhooks.py` | Receipt identity independent of call retention |
| `migrations/versions/20260930_0002_webhook_receipts.py` | Versioned schema addition |
| `roma/repositories/redis/call_status.py` | WATCH/MULTI projection preserving terminal status/outcome/TTL |
| `roma/main.py` | Database/service construction and shutdown |

## Failure contract

First delivery commits receipt/call/event together. Concurrent duplicates wait/observe the unique identity and do not create another effect. Failed transactions roll back and return 503; configure provider retries rather than assuming a 503 enables them. An ambiguous commit response is safely retried with the same identity.

Same identity with changed normalized business input returns 409. Duplicates return deterministic TwiML for the same input/configuration and `X-Webhook-Duplicate: true`. Raw payloads, phone numbers and lead tokens are not stored in receipts. Redis failure cannot undo the database commit; a later accepted delivery may repair its status projection.

Minimal receipts survive call deletion and have no automatic purge. Removing them reduces deduplication coverage and may allow replay. A signed old request is deduplicated; this is not timestamp freshness rejection. Answer identity is endpoint/account/call-specific; other callback kinds need their own identity/retention contract.

## Evidence and exercise

Use `tests/api/v1/test_webhook_idempotency.py`, `tests/repositories/postgres/test_webhooks.py` and `tests/repositories/redis/test_call_status.py`. Disposable PostgreSQL proves constraints/isolation/rollback that dictionary mocks cannot. Fresh execution status is in [baseline evidence](roadmaps/level-00-baseline.md).

In a disposable test, send the same synthetic signed form twice, then concurrently; inspect one receipt and one `twilio.answer` event. Inject rollback before commit, retry, then verify changed-input conflict and late-answer terminal status. Do not contact Twilio or inject failure into the working database to learn this contract.

Explain why SELECT-then-insert races, why receipt/effect must share a transaction, why Redis SET NX cannot replace SQL authority, why Call SID alone is insufficient for every callback, and how lost acknowledgements and retention alter guarantees.

L8 still needs complete user access, replay/privacy policy, rate-limit coverage and new callback controls where required. See [security](07-security.md), [jobs](19-background-task-framework.md) and [L8](levels/level-08.md).
