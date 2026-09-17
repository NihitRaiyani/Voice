# 03 — Conversation Phase Machine

Plain Python. The machine decides **what happens next**; the LLM only decides **how to say
it**. It never picks the phase, the slot, or "objection handled." That is what makes it fixed.

## Call-state object (keyed by Vobiz Call SID in Redis)
```
lead_name = None at call start — CAPTURED IN P2, never preloaded
course = "digital_marketing" (fixed)
education · passing_year · current_status · city · timing_constraint
placement_interest · mode_pref
slot_attempts{} · objection_counts{} · slots_offered[] · locked_slot
phase · turn_count · phase_turn_count
```
Roma is **inbound**: the caller rang us and nothing is known about them at connect. `lead_name`
starts `None` and is filled during discovery like any other slot. There is no `course_interest`
— Weltec sells one course, and a field nothing filled and nothing read was deleted
(docs/decisions.md, 2026-07-31).

## The 7 phases
| # | Phase | Job | Word cap | Filter strictness |
|---|---|---|---|---|
| P1 | Open | **branch:** only if they ask who picked up, say Weltec again | 25 | low |
| P2 | Discover | 5 slots, fixed order, name first | 20 | low |
| P3 | Value | fixed KB-grounded pitch | 120 | **high** (outcome-claim zone) |
| P4 | Structure | batches, modes, no money | 60 | **max** (money zone) |
| P5 | Pivot | offer TWO concrete slots | 30 | high |
| P6 | Objection | answer → route money to visit | 45 | **max** |
| P7 | Close | readback + lock | 25 | high |

## Discovery order (P2) — fixed, one slot per turn, never batched
`lead_name → current_status → education → passing_year → city`

The authority is `DISCOVERY_ORDER` in `controller/state.py`; this line follows it, not the
other way round. Two departures from the original design, both bought on live calls:

* **`current_status` before `education`**, asked with its three options in it. "Aapne padhai
  kahan tak ki hai?" is too open — it gets "college tak" back, which tells the machine nothing
  and leaves the model filling the gap itself.
* **`timing_constraint` is not asked at all.** It was last on purpose, to set up P4 — but
  asking it pulled the very next turn into "subah das ya gyaarah baje theek rahega?", an offer
  in a phase whose prompt carries no OFFER line and where hard rule ten forbids exactly that.
  P4 dissolves the timing objection without having asked for it first.

**Attempt cap.** `next_discovery_slot()` drives *extraction*, not the question, so a slot the
caller will not answer used to pin the pointer and swallow every slot behind it. A slot tried
`SLOT_ATTEMPT_CAP` (2) times is skipped. The trade is that a value volunteered later is not
picked up — correct against losing the rest of discovery, but real.

**P1 does not speak first, and usually does not speak at all.** The opener is cached audio
(`canned.OPENING_LINE`, played at connect), so by the time the P1 *fragment* can run, Roma has
already said "Hello, Weltec Institute". P1 is therefore a branch with one job: if the caller
asks who picked up, say it again. Everyone else is already in P2.

**Its exit is inbound-shaped.** It used to require an affirmation, because outbound asked a
real question — "kya abhi 2 minute baat kar sakte hain?" — and needed a real yes. Inbound
inverts it: they dialled us and waited, so the call *is* the inquiry, and anything substantive
they say confirms it. A caller who opens with "course ki information chahiye thi" never says
"haan", and under the old rule sat in P1 for the entire call.

Two replies do NOT advance: a short bare negation (a wrong number), and an identity question
(`asks_who_we_are` — "kaun bol raha hai", or naming us back in three tokens or fewer). The
second matters because P2's first question is the caller's NAME, and answering "who are you?"
with "what's your name?" is the rudest turn in the call.

## Transitions (all deterministic)
| From | Fires when | To |
|---|---|---|
| P1 | caller states their business (or affirms) | P2 |
| P2 | all slots filled OR phase_turn_count > `len(DISCOVERY_ORDER)` | P3 |
| P3 | after 1 turn | P4 |
| P4 | after 1 turn | P5 |
| P5 | slot accepted | P7 |
| P5 / P7 | objection classifier fires | P6 |
| P6 | objection answered | back to **P5** (never backward) |
| P6 | same objection twice | P5 with HARD pivot, no re-answer |
| P7 | locked_slot != null AND readback confirmed | **terminal = WIN** |

The `P5 / P7 → P6` row is the one sanctioned index-backward move — an objection outranks
every phase, including the close (the classifier runs before phase dispatch in
`machine.next_phase`). "Never backward" is a claim about the **P6 exit**: an answered
objection returns to the booking, never to discovery or the pitch. Structure questions
raised early do not move the phase at all — P2 and P6 carry a compact approved fact list
and answer them inline.

The objection classifier is a small fast call or a lexicon — NOT the main LLM deciding its
own state.

## The five places the machine beats the human source calls
1. **P5 always offers two named slots** ("Monday saanjhe 6 ke Tuesday sawaare 11?"). The
   human counsellors never did this — every "lock" was lead-initiated. Highest-value change.
2. **P7 readback is mandatory.** "Monday, 6 baje, Sayajigunj — confirm?" No readback, no win.
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
