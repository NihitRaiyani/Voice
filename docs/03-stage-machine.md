# 03 — Conversation Stage Machine

**Status:** Implemented in the controller with Redis-backed live state. Durable recovery through
PostgreSQL is planned.

**Learning objective:** Model business flow as explicit states, events, transitions, and
invariants. The LLM can phrase a response but cannot authorize a transition.

Plain Python. The machine decides **what happens next**; the LLM only decides **how to say
it**. It never picks the stage, the slot, or "objection handled." That is what makes it fixed.

## Call-state object (keyed by Twilio Call SID in Redis)
```
lead_name = None at call start — CAPTURED IN discover, never preloaded
course = "digital_marketing" (fixed)
education · passing_year · current_status · city · timing_constraint
placement_interest · mode_pref
slot_attempts{} · objection_counts{} · slots_offered[] · locked_slot
stage · turn_count · stage_turn_count
```
Roma is **inbound**: the caller rang us and nothing is known about them at connect. `lead_name`
starts `None` and is filled during discovery like any other slot. There is no `course_interest`
— Weltec sells one course, and a field nothing filled and nothing read was deleted
(docs/decisions.md, 2026-07-31).

## The 7 stages
| Stage | Job | Word cap | Filter strictness |
|---|---|---|---|
| `open` | **branch:** only if they ask who picked up, say Weltec again | 25 | low |
| `discover` | 5 slots, fixed order, name first | 20 | low |
| `value` | fixed KB-grounded pitch | 120 | **high** (outcome-claim zone) |
| `structure` | batches, modes, no money | 60 | **max** (money zone) |
| `pivot` | offer TWO concrete slots | 30 | high |
| `objection` | answer → route money to visit | 45 | **max** |
| `close` | readback + lock | 25 | high |

`roma.domain.conversation.stage.ConversationStage` is the single runtime and persisted vocabulary:
`open → discover → value → structure → pivot → objection → close`. Application code uses the facade in
`roma.domain.conversation.state_machine`: `get_state`, `transition`, `can_transition`,
`save_state`, `restore_state`, `handle_interruption`, `handle_objection`, and
`handle_missing_information`. This keeps business-state ownership in the domain layer while Redis
remains only a checkpoint adapter.

## Discovery order (discover) — fixed, one slot per turn, never batched
`lead_name → current_status → education → passing_year → city`

The authority is `DISCOVERY_ORDER` in `roma/domain/conversation/state.py`; this line follows it, not the
other way round. Two departures from the original design, both bought on live calls:

* **`current_status` before `education`**, asked with its three options in it. "Aapne padhai
  kahan tak ki hai?" is too open — it gets "college tak" back, which tells the machine nothing
  and leaves the model filling the gap itself.
* **`timing_constraint` is not asked at all.** It was last on purpose, to set up structure — but
  asking it pulled the very next turn into "subah das ya gyaarah baje theek rahega?", an offer
  in a stage whose prompt carries no OFFER line and where hard rule ten forbids exactly that.
  structure dissolves the timing objection without having asked for it first.

**Attempt cap.** `next_discovery_slot()` drives *extraction*, not the question, so a slot the
caller will not answer used to pin the pointer and swallow every slot behind it. A slot tried
`SLOT_ATTEMPT_CAP` (2) times is skipped. The trade is that a value volunteered later is not
picked up — correct against losing the rest of discovery, but real.

**open does not speak first, and usually does not speak at all.** The opener is cached audio
(`canned.OPENING_LINE`, played at connect), so by the time the open *fragment* can run, Roma has
already said "Hello, Weltec Institute". open is therefore a branch with one job: if the caller
asks who picked up, say it again. Everyone else is already in discover.

**Its exit is inbound-shaped.** It used to require an affirmation, because outbound asked a
real question — "kya abhi 2 minute baat kar sakte hain?" — and needed a real yes. Inbound
inverts it: they dialled us and waited, so the call *is* the inquiry, and anything substantive
they say confirms it. A caller who opens with "course ki information chahiye thi" never says
"haan", and under the old rule sat in open for the entire call.

Two replies do NOT advance: a short bare negation (a wrong number), and an identity question
(`asks_who_we_are` — "kaun bol raha hai", or naming us back in three tokens or fewer). The
second matters because discover's first question is the caller's NAME, and answering "who are you?"
with "what's your name?" is the rudest turn in the call.

## Transitions (all deterministic)
| From | Fires when | To |
|---|---|---|
| open | caller states their business (or affirms) | discover |
| discover | all slots filled OR stage_turn_count > `len(DISCOVERY_ORDER)` | value |
| value | after 4 turns (`VALUE_MAX_TURNS`) | structure |
| structure | after 1 turn | pivot |
| pivot | slot accepted | close |
| any stage | objection classifier fires (below the repeat cap) | objection |
| objection | objection answered | back to **pivot** (never backward) |
| objection | same objection twice | pivot with HARD pivot, no re-answer |
| close | locked_slot != null AND readback confirmed | **terminal = WIN** |

Objection handling outranks normal progression in every stage, including the opening and close
(the classifier runs before stage dispatch in
`machine.next_stage`). "Never backward" is a claim about the **objection exit**: an answered
objection returns to the booking, never to discovery or the pitch. Structure questions
raised early do not move the stage at all — discover and objection carry a compact approved fact list
and answer them inline.

The objection classifier is a small fast call or a lexicon — NOT the main LLM deciding its
own state.

## The five places the machine beats the human source calls
1. **pivot always offers two named slots** ("Monday saanjhe 6 ke Tuesday sawaare 11?"). The
   human counsellors never did this — every "lock" was lead-initiated. Highest-value change.
2. **close readback is mandatory.** "Monday, 6 baje, Sayajigunj — confirm?" No readback, no win.
3. **Objection loop capped at 2.** Second repeat → stop answering, hard pivot to slots.
4. **Landmark/directions move AFTER the lock.** Address goes to WhatsApp post-lock, never as
   a substitute for closing.
5. **Every turn ends with a question or a concrete proposal — never a statement.** Hard rule.

## Slot extraction (the win condition's parse)
- Time words are idiomatic: `dhai baje`=2:30, `sawa paanch`=5:15, `paune chaar`=3:45,
  `saanjhe`=evening, `kal`=tomorrow. Use OpenAI structured output, NOT regex/dateparser.
- Resolve relative→absolute in code, Asia/Kolkata, with today's date injected. Never let the
  model do date arithmetic.
- `confidence < threshold` → re-ask, do not guess. A confidently-wrong slot burns the lead.

## Roadmap bridge

Persist stage transitions and booking progress as durable events/checkpoints, then test invalid
transitions and resume behavior. The key interview distinction is **probabilistic language inside
deterministic business control**.
