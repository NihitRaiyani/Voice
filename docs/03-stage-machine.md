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

## Database-backed production confirmation

The PostgreSQL appointment repository locks the existing slot and enforces one active `booked` or `confirmed` appointment per branch/date/start time. Production composition injects `BookingSession` as the call calendar and confirmation gateway. It holds verified choices in Redis, validates caller-named times and commits on deterministic caller affirmation. A model's `readback_confirmed` field alone cannot authorize the commit.

The controller clears any tentative lock while awaiting the commit and only then sets `appointment_id`, `locked_slot` and win. Conflict, storage failure or interruption cannot announce a new confirmed appointment. Production prompt policy removes unlimited availability and WhatsApp delivery promises; the pre-TTS gate requires committed identity. Existing static/read-only calendars and conversational locks remain isolated offline baselines.

Section 13 reconciles same-SID appointment state from PostgreSQL before prompts, restores committed ID/time after an uncertain Redis checkpoint and removes false locks without a durable booking. Pending candidates must reacquire/check a hold. This does not create new-SID redial identity or pass Level 7 recovery drills. See [booking policy and evidence](levels/level-03.md) and [data contract](14-data-and-concurrency.md).
