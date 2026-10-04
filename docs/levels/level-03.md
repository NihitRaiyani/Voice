# Level 3 — Transactional booking

**v4 sections:** 13. **Status:** Repository foundation implemented; live/API commit, cancellation and full gate pending.

## Entry gate

Level 1 persistence plus Level 2 text/confirmation contract.

## Scope when implementation is requested

Complete PostgreSQL appointment authority in the live/API application path using the existing repository. Use a short transaction to check/lock where applicable, recheck and commit. Enforce the existing partial unique branch/date/start-time invariant for active `booked` and `confirmed` appointments; a row lock cannot protect a row that does not exist. Define cancellation, timezone, availability and conflict responses explicitly.

Redis NX/expiry holds are advisory; 90 seconds is a roadmap example requiring policy review. Handle expiry and concurrent attempts using database truth. Return HTTP 409 on a slot conflict and propose another valid slot.

Reconcile prompt claims, controller state and readback/affirmation gates with real availability. Success needs caller confirmation and a durable commit; external calendar/CRM synchronization remains asynchronous.

Current `capacity` field does not allow multiple active bookings under the index. Cancellation must release the slot as well as changing appointment status. Review these policies with the counselling owner.

## Existing reuse in Voice_Agent

PostgreSQL `AppointmentRepository.book`, existing-slot FOR UPDATE lock, active-slot partial unique index and the 100-request repository race test. Live static/read-only calendar still needs integration.

## Acceptance gate

- [x] 100 concurrent attempts for one eligible slot yield exactly one reservation and 99 conflicts.
- [ ] Cancellation frees a slot; stale/expired holds cannot override the database.
- [ ] Retry/idempotency and commit-failure cases never speak a false confirmed booking.
- [ ] No transaction stays open during inference, audio or external sync; coordinated prompt/controller regressions pass.

## Boundaries and advanced work

No Redis-only booking authority or replacement of explicit confirmation with extracted intent.

## Evidence

The complete offline suite passed on 2026-10-04, including the existing repository 100-attempt race. Live/API integration, cancellation and confirmation/commit behavior remain pending.

Record revision, reproducible commands, environment/data/configuration identity, actual outcomes, demo/reviewer result and limitations here when this level is implemented. Existing foundations do not automatically pass the full gate. Use the [verification contract](../12-verification.md), [baseline](../roadmaps/level-00-baseline.md) and [decision register](../decisions.md).

Return to the [documentation index](../README.md).
