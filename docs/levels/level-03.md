# Level 3 — Transactional booking

**Roadmap section:** 13. **Status:** PostgreSQL booking, cancellation/reuse, advisory Redis holds and the complete offline controller-to-database flow are implemented and verified. Paid/live voice approval remains deferred.

The 2026-10-22 owner-authorized [supervised follow-up](level-06.md#supervised-claude-and-booking-follow-up--2026-10-22) adds one real English microphone readback/consent, confirmed TEST ONLY appointment and authoritative SQL restore. Natural explicit consent was repaired with controller regressions and no extra LLM call. The disposable database was removed after evidence collection; working `roma`, production telephony and schema remain unchanged. Formal native/representative acceptance remains pending.

## Entry gate

Reuse Level 1 PostgreSQL/Alembic and the Level 2 state/readback/safety seams. This section does not retrospectively approve deferred clean-host, native-speaker or voice latency gates.

## Implemented flow

`BookingService` reads candidate availability with a short session, then obtains a Redis lease and rechecks SQL after that lease. Each session closes before Redis or model/audio work. On explicit caller affirmation, `BookingSession.confirm()` invokes a short PostgreSQL transaction: lock the call when present, lock the existing slot, recheck availability, insert the appointment, mark the slot booked and update the call's booking status. Commit precedes controller win, checkpoint and confirmed speech. REST uses the same service/store.

The existing `uq_appointments_active_slot` partial unique index protects `(branch_id, appointment_date, start_time)` for **both `booked` and `confirmed`**. This is stronger than the roadmap's confirmed-only example and remains unchanged. The composite slot foreign key prevents bookings without a configured slot. No migration, working-DB seed or schema rewrite is needed.

## Decisions and failure behavior

- PostgreSQL is authoritative. Capacity remains one active appointment regardless of the slot's positive `capacity` field. Row locking makes contenders wait; the partial unique index also rejects direct competing inserts that bypass the application. Optimistic insert-and-handle-conflict is a valid alternative, but replacing the working pessimistic path adds no benefit here.
- A hold uses `hold:{branch}:{date}:{time}` with a random ownership token plus call ID, atomic Redis `SET NX EX`, and a default **90-second** TTL. Same-owner retries return remaining time without refreshing it. Compare-and-delete cleanup cannot release another owner's hold. Offering two choices holds at most two when returned (a replacement may briefly overlap its predecessor during refresh); selection, refusal and normal teardown release unused holds. TTL covers lost cleanup.
- Redis is outside database transactions. New offers fail safely if holds cannot be acquired. At confirmation an absent/expired hold or Redis outage still requires the locked SQL recheck; another readable owner causes conflict. Failed cleanup after commit cannot turn a real booking into a reported failure.
- `BOOKING_HOLD_TTL_SECS` defaults to 90 (1–300); database operation deadline defaults to 3 seconds (maximum 10), Redis operation deadline to 1 second (maximum 3). These are failure bounds, not measured latency targets. The DB deadline includes pool checkout.
- Dates use **Asia/Kolkata**, start time **10:00 inclusive to 18:00 exclusive**, minimum lead **90 minutes**, horizon **14 days**. The roadmap's 6 PM example illustrates a race, not a change to existing visiting hours. Only active, unambiguous configured IST branches and exact configured slots can be offered; empty reference data yields no invented availability.
- A caller-affiliated booking locks the call to serialize different-slot attempts, allowing one active appointment per call through this application path. The database-wide invariant remains one active appointment per slot. Direct SQL bypasses the application-only per-call policy.
- API clients may send a UUID `idempotency_key`; identical active retries return the same appointment ID. Changed identities or terminal appointment reuse conflict. Live confirmation uses a deterministic appointment ID derived from the durable call ID. Same-ID retries are checked inside the slot transaction as well.
- Cancellation locks the call/slot/appointment, changes appointment status and frees a booked slot atomically. Repeated cancellation of an old appointment cannot unlock its successor. Terminal records cannot be reactivated with PATCH; create a new booking identity. Completion/no-show retains the consumed slot. Rescheduling is outside this section.
- Same-SID reconnect reads active PostgreSQL appointments before prompts. It restores committed ID/time despite stale Redis, and removes checkpoint-only false locks. It rechecks/reacquires a pending candidate hold. Unknown/ambiguous branches, missing calls and unavailable SQL never become a confirmed visit. A new-SID redial remains Level 7 work.
- Hindi-base Hinglish remains production policy. Production prompts remove the static baseline's unlimited-availability claim and delivery promise. The pre-TTS gate requires a committed appointment identity for confirmation. The static/read-only calendar remains explicitly available to offline tests. All speech retains the section 12 safety/audit gate.

## Files and application interfaces

| Area | Main files |
|---|---|
| Transaction/index reuse and cancellation | `roma/repositories/postgres/repositories.py`, `models/appointments.py`, `booking.py` |
| Advisory ownership | `roma/domain/appointments/booking.py`, `roma/repositories/redis/appointment_holds.py` |
| Orchestration and call calendar | `roma/services/booking_service.py`, `booking_session.py`, `rest_api_service.py` |
| Protected HTTP contracts | `roma/schemas/rest_api.py`, `roma/api/v1/rest.py`; [API details](../24-rest-api-design.md#appointment-holds-and-confirmation) |
| Production wiring | `roma/main.py`, `roma/realtime/pipeline.py`, `pretts.py` |
| Confirmation truth | `roma/domain/conversation/turn.py`, `state.py`, `machine.py`, `shortcircuit.py`, `prompts.py`, `roma/prompts/booking_policy.md` |
| Offline opt-in seam | `roma/services/text_conversation_service.py`; a disposable integration test makes and restores one durable demo booking without touching configured `roma` |

## Acceptance gate and evidence

- [x] Native disposable PostgreSQL: 100 simultaneous HTTP booking attempts produce exactly **1 HTTP 201 and 99 HTTP 409**, with one appointment and a booked slot.
- [x] Retain the earlier 100-attempt repository race and existing partial unique index; bypassed application writes still meet database uniqueness.
- [x] Cancellation/rebooking, stale cancellation, stable-ID retries, terminal rejection and actual commit failure/rollback are covered.
- [x] Native disposable Redis: 100 hold contenders have one winner; expiry, unchanged retry TTL and stale ownership cleanup are covered. Two call calendars receive distinct held choices; a lost hold cannot override another owner or SQL truth.
- [x] Controller tests cover commit-before-win, interrupted/failed confirmation, deterministic caller affirmation and database-authoritative restore. SQL connections are released before mocked Redis waits.
- [x] Complete offline conversation path: a caller selects a disposable demo slot through `TextConversationService`, hears the exact readback, affirms it, commits one appointment, restores it from PostgreSQL and receives confirmed speech. A second caller cannot take the booked slot and is offered the remaining verified slot.
- [x] Full regression, Ruff/mypy, commit hooks and documentation-link evidence recorded below.
- [ ] Owner/teacher demonstration with approved real reference slots and later complete voice approval. No paid call, production seed or model download is authorized by these tests.

Focused tests: `tests/repositories/postgres/test_booking_module.py`, `tests/repositories/redis/test_appointment_holds.py`, `tests/services/test_booking_service.py`, `tests/domain/conversation/test_booking_commit.py`, `tests/realtime/test_booking_commit.py`, `tests/api/v1/test_booking_resources.py`. These run in `make test-ci` and hosted CI, using native disposable services and mock providers. The full offline suite also retains `test_appointment_concurrency.py`.

The 2026-10-21 conversation test uses two disposable calls, an active `Test` institute/`Vadodara` branch and 11:00/17:00 next-day slots inside a disposable PostgreSQL cluster plus local disposable Redis. It performs no Claude, microphone, ASR, TTS, network or configured-database operation. The transaction, uniqueness and restore path passed. The stronger spoken-result assertion exposed one controller edge: after a conflict the confirmation guard replaced the whole line and dropped the verified alternative slot. Reusing the approved re-offer prefix fixes that interaction, and the pronunciation regression proves the replacement remains finite for local TTS. This closes the offline booking-flow proof, not the later human/voice acceptance gate.

Combined follow-up verification on 2026-10-21 passed **114 focused provider/voice/booking tests** and **2,757 full offline tests**. All pre-commit hooks passed; Markdown validation checked **69 files / 462 local links and anchors** with zero issues. No paid provider or microphone call ran.

Verification on 2026-10-17, using the locked environment, synthetic fixed-clock inputs and disposable native databases/caches:

| Check | Result |
|---|---|
| Full offline pytest suite | **1,825 passed**, 23 existing dependency deprecation warnings, 75.30 seconds |
| `make test-ci` | **56 provider/config tests + 245 bounded API/unit/native persistence tests passed** |
| `make lint type` | Ruff clean; mypy **36 source files** clean |
| Markdown links/anchors | **64 non-archive Markdown files, 319 local links/anchors, 0 issues** |
| Pre-commit hooks | Ruff, mypy and mock provider contracts passed |
| Existing Word roadmap | SHA-256 unchanged (`89b99bbf959d1cc713d335a49037a03b63349cc91185a03e0a57bf9fa3149429`) |

Reproduce the full suite with `make test` after the [runbook prerequisites](../runbook.md). HTTP test fixtures disable owner `.env` loading and supply dummy provider settings explicitly; no real API key is needed. The bounded CI slice includes the new booking tests and production composition test; [GitHub CI](https://github.com/NihitRaiyani/Voice/actions/workflows/ci.yml) runs it on each push. These are correctness checks, not paid end-to-end voice timing or production load measurements.

A read-only check on 2026-10-17 confirmed configured database `roma`, revision `20261004_0003` at head, 25 application tables and 0 application rows (including branches, slots and appointments). Real reference data and slots therefore still need approved provisioning before live booking. Disposable tests do not mutate this database. See [operating guidance](../runbook.md#section-13-booking-operations), [data contract](../14-data-and-concurrency.md), [verification](../12-verification.md) and [decisions](../decisions.md).

Return to the [documentation index](../README.md).
