# 14 — Data, Transactions, and Concurrency Tutorial

**Status:** Target design. Redis is implemented; PostgreSQL schemas and appointment transactions
are **Next**.

## Why two datastores

Redis and PostgreSQL answer different questions.

| Question | Best owner |
|---|---|
| What stage is this active call in for the next few minutes? | Redis |
| Has this appointment been committed and must it survive restarts? | PostgreSQL |
| Is this cached audio already rendered? | Redis |
| Which safety rules fired last month? | PostgreSQL |
| Has this short-lived rate limit been exceeded? | Redis |
| Who changed an appointment and when? | PostgreSQL |

The learning outcome is the decision, not merely knowing two database products.

## Suggested relational model

| Table | Responsibility | Important constraints/indexes |
|---|---|---|
| `callers` | Minimal caller profile | unique phone hash where appropriate |
| `institutes`, `branches`, `courses` | Reference data | stable keys; branch/timezone indexes |
| `calls` | One provider call lifecycle | unique provider call SID; status/time indexes |
| `call_turns` | Caller/agent turns | unique `(call_id, turn_number)` |
| `call_events` | Append-only lifecycle events | `(call_id, occurred_at)` index |
| `appointment_slots` | Offerable branch times | unique branch/date/start time |
| `appointments` | Confirmed/rescheduled/cancelled visits | one active booking per slot |
| `safety_events` | Guardrail evidence | call, rule, and time indexes |
| `provider_usage` | Raw STT/LLM/TTS/Twilio usage | provider/call/turn indexes |
| `call_costs` | Normalized ledger entries | currency and pricing-version fields |
| `recordings` | Metadata, not public blobs | retention/deletion status |
| `followup_jobs` | Durable business jobs | unique idempotency key |
| `users`, `roles` | Human access model | unique identity; role relationships |
| `audit_logs` | Administrative actions | actor/action/resource/time indexes |

Students must decide primary keys, foreign keys, nullability, cascade behavior, indexes, retention,
and which facts are mutable. A table existing is not proof that its model is correct.

## Appointment invariant

Two callers can read the same free slot before either writes. "Check then insert" without a
transaction is a race.

```text
Caller A reads free ----+                 +---- Caller B reads free
                       |                 |
                       +-- both attempt -+
                                 |
                    database invariant decides
                                 |
                     one commit, one conflict
```

Recommended invariant:

```sql
UNIQUE (branch_id, appointment_date, start_time)
```

The booking use case should:

1. begin a transaction;
2. select/lock or otherwise claim the candidate slot;
3. re-check availability inside the transaction;
4. create the appointment and mark the slot booked atomically;
5. commit if the invariant holds;
6. roll back and return a domain conflict mapped to HTTP `409` if it does not.

Compare pessimistic row locking with optimistic/version-based control. Choose based on contention,
transaction length, and database behavior—not fashion.

## Durable conversation state

Redis checkpoints keep a live call fast. PostgreSQL should later preserve the durable narrative:

- call lifecycle and final outcome;
- turn number, speaker, transcript policy, stage, route, and latency;
- stage-transition events;
- appointment offers, acceptance, and confirmation;
- safety and provider-usage events.

Do not write every audio frame or token to PostgreSQL. Persist business-significant checkpoints and
events outside the latency-sensitive path.

## Migrations

Use Alembic so schema change is versioned and reviewable. A healthy migration sequence has forward
behavior, a rollback or recovery strategy, indexes introduced deliberately, and seed/demo data
separate from schema definition.

## Required exercises

1. Draw the ER diagram and justify every relationship.
2. Run migrations against an empty database.
3. Demonstrate an index with an actual query plan.
4. Write an integration test that rolls back a failed booking.
5. Launch 100 booking attempts for one slot and assert one success plus 99 conflicts.
6. Explain why an application-level `if available` check cannot replace a database constraint.
