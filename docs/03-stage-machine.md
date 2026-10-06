# Conversation stages and booking truth

`ConversationStage` in `roma/domain/conversation/stage.py` is the canonical runtime/checkpoint vocabulary. v4 development levels are engineering increments; conversation stages are runtime business state. Never mix those terms.

| Stage | Responsibility | Current word cap |
|---|---|---|
| `open` | Direction-specific identity/permission handling | 25 |
| `discover` | One discovery field per turn | 20 |
| `value` | Approved Digital Marketing explanation | 120 |
| `structure` | Approved modes/batches without money claims | 60 |
| `pivot` | Two concrete valid visit proposals | 30 |
| `objection` | Bounded approved response, then return to booking | 45 |
| `close` | Readback and affirmative confirmation | 25 |

Discovery order is name → current status → education → passing year → city, with two attempts per slot. Missing facts remain unknown. Do not invent a degree or ask an extra timing-constraint discovery question. Preserve anti-repetition, relevant questions/proposals and the five-minute ceiling; terminal sign-off is exempt from requiring another question.

## Deterministic progression

Normal flow is open → discover → value → structure → pivot → close. Objection detours return to pivot; repeat caps prevent loops. Explicit booking requests, deferrals, factual questions and pacing can take tested shortcuts. `machine.py`, `turn.py` and the state-machine facade own the executable conditions and event priority. Level 2 section 10 exposes `get_state()`, `transition()`, `can_transition()`, `save_state()`, `restore_state()`, `handle_interruption()`, `handle_objection()`, `handle_missing_information()` and `route_intent()` so application code depends on the machine boundary rather than free-form prompt state.

Inbound connects with a cached opener. Outbound lead custom parameters establish direction even if Redis lookup fails. Shared prompt/opening behavior must be validated together; do not assume all inbound/outbound policy claims in old docs remain true.

## Slots and confirmation

Structured extraction returns candidate meaning; code resolves relative dates/idiomatic times using Asia/Kolkata and an injected clock. Current visit-start window is 10:00 inclusive to 18:00 exclusive. Reject past, refused, unclear and out-of-hours times. Do not treat proposed 11:00/17:00 slots as the entire opening-hours policy.

A visit needs specific branch/day/time, readback and affirmative confirmation. A correction invalidates stale confirmation. Directions/address follow the confirmed visit; vague intent is not success.

## Repository versus live integration

The PostgreSQL appointment repository locks an existing slot and enforces one active `booked` or `confirmed` appointment per branch/date/start time. The live controller still uses static hours/optional read-only calendar and a conversational `locked_slot`; it does not call the transactional booking repository.

At Level 3 wire short reservation/confirmation commits into the controller/application boundary and expose conflicts safely. Change always-available prompt claims and confirmation tests together. Success must reflect committed database truth; Redis holds never substitute for it. Complete cancellation/reuse semantics, slot capacity and request idempotency before passing the gate.

Level 2 section 10 adds PostgreSQL latest-checkpoint save/restore for compatible same-call state. A Redis checkpoint is still the hot path, and this does not claim redial recovery, appointment reservation restore or L7 recovery guarantees. See [data tutorial](14-data-and-concurrency.md) and [Level 3](levels/level-03.md).
