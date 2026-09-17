# 11 — Prompts

The prompt text is **runtime data, not documentation**. It lives in `src/roma/prompts/` and is
loaded at startup. This doc is the contract for how it is structured and assembled.

## Layout
```
src/roma/prompts/
  persona.md              always loaded
  hard_rules.md           always loaded
  phases/
    p1_open.md            exactly ONE of these is loaded per turn
    p2_discover.md
    p3_value.md
    p4_structure.md
    p5_pivot.md
    p6_objection.md
    p7_close.md
```

One file per phase because the file is the unit of loading. A single large file would mean
parsing sections at runtime — fragile, and it breaks the caching order below.

## Why split at all
A single mega-prompt makes the model average across every phase's job at once. Loading one
phase fragment means it only ever sees the current job. This gets the benefit people reach for
with "sub-agents" **without extra LLM calls** — which matters because every added call eats the
latency budget (`docs/02`). Consistent with the LangGraph rejection: the phase is state, not an
agent.

## Assembly order per turn — this order is load-bearing
```
1. persona.md            }  byte-identical on every call, every phase
2. hard_rules.md         }  → best prompt-cache hit
3. phases/pN.md             static per phase (7 possible prefixes)
4. call-state + recent turns   VARIABLE — must come last
```
Prompt caching only applies to a byte-identical prefix. Put call-state anywhere earlier and the
discount is lost on every turn. Do not reorder for readability.

## Word caps → max_tokens
The controller sets `max_tokens` from the phase's cap (`docs/03`). This caps monologuing, cost,
and latency together. Caps: P1 25 · P2 20 · P3 70 · P4 60 · P5 30 · P6 45 · P7 25.

## Filter risk per phase
P3 (outcome claims) and P4/P6 (money) are the high-risk phases — the pre-TTS filter (`docs/04`)
applies maximum strictness there. The prompt is the first line of defence; the filter is the
one that must not fail.

## Template variables
`{{branch}}` (Vadodara or Ahmedabad — the branch they visit, distinct from the lead's city) is
the only **preloaded** one. Roma is inbound: the caller rang us and nothing about them is known
at connect.

`{{lead_name}}` is empty at call start and captured as the FIRST discovery slot in P2. No
fragment substitutes it — the name reaches the model each turn inside `{{known}}` (the PATA
HAI line), which is restated from the machine's own state and therefore survives a barge-in.
P1 deliberately never carries a name: greeting a stranger by one is the outbound behaviour
this was converted away from, and on a real inbound call there is no name to greet with.

`{{course_interest}}` was **deleted** (2026-07-31). Nothing preloaded it, no fragment read it
after the P1 rewrite, and Weltec sells one course — `course = "digital_marketing"` in
call-state already says so. A variable nothing fills and nothing reads is a trap for the next
reader.

## Editing
Changing Roma's wording is a text-file edit, not a code change. But: any edit to `persona.md`
or `hard_rules.md` invalidates the cached prefix for every phase — expect a brief cost/latency
bump after a change, and avoid churn on those two files.

## The prompt budget — why the rules no longer carry their own stories

`persona.md` + `hard_rules.md` are re-sent on **every turn of every call**. Measured against
`var/roma/spend.jsonl` on 2026-08-01 (121 requests, 17 calls, ₹89.20), that makes them ~60% of
a call's cost, and gives a flat exchange rate:

> **~₹0.28 per 100 prefix tokens, per 22-turn call.**

The prefix was cut 3,372 → 2,754 tokens on 2026-08-01, and the two largest fragments with it
(`p3_value` 1,379 → 1,212, `p2_discover` 986 → 880). A 22-turn call goes ~₹15.62 → **~₹13.7**;
the real 13-turn call in the ledger goes ₹9.39 → ₹8.19. Fewer prefix tokens also means less to
process per turn, so LLM TTFB falls with the cost.

**What was cut was never a rule.** Each rule had grown a paragraph of live-call incident
history explaining why it existed. The model needs the imperative; the incident is for us, and
here it is free. `test_the_cached_prefix_stays_within_its_token_budget` is the ratchet that
stops the prose growing back.

The incidents, preserved:

- **hard rule 5 (bot disclosure).** On `CA3cc181ed` Roma said "call bhi record ho raha hai" in
  the middle of an unrelated answer nobody had prompted, alongside "main phone pe number nahi
  de sakti" — a refusal to something the lead had not asked for either. Both made her sound
  defensive on a call where the lead was simply waiting to hear about the course. Recording
  disclosure is a compliance line played at call start or not at all.
- **hard rule 8 (branch timings).** Roma told a live lead the branch runs "gyaarah se paanch",
  reading the two OFFER times as opening hours, then refused a noon visit — with the branch
  open ten to six. The hour-by-hour enumeration that used to spell out every bookable hour was
  redundant with the stated das-se-chhe window and is gone; the rule is not.
- **persona, repeated facts.** Roma said "faculty working professionals hain jo abhi industry
  mein kaam kar rahe hain" and, one turn later, "faculty jo sikhate hain wo khud industry
  professionals hain"; she answered the placement-guarantee question twice in twenty seconds.
  The lead's complaint was that the course details repeat.
- **persona, openers.** Roma opened with "Achha" twice and then "Bilkul" twice on one call.
  The words are fine; the lead's complaint was the repetition.
- **persona, apologies.** "maaf kijiye" four times in five turns on one call, "mujhe khed hai"
  on the next. Neither call reached a booking.
- **`p2_discover`, guessed degrees.** Twice Roma answered "college tak padha hai" with "BCom
  kiya hai toh" and the lead had to correct her — "maine B.Com bola hi nahi aapko".
- **`p3_value`, the qualifying question.** Asked before any course substance, the lead replied:
  "आपने यह तो फिर भी नहीं मुझे बताया कि आप कौन से course के बारे में बात करने आए हो, क्या
  benefits है, क्या हम course में आके पढ़ेंगे — यह सब आपने मुझे कुछ नहीं बताया."
- **`p5_pivot`, asking availability early.** It used to be asked back in discovery, where it
  primed the booking a phase early and pushed the course explanation past the lead's patience
  (`CA3cc181ed`).

One phrase turned out to be load-bearing and was restored: `p4_structure` must name
"saade paanch se saat" literally, because the fragment's job is to mark that exact string as a
CLASS time and not a visit slot (`test_the_structure_fragment_labels_its_timings_as_class_not_visit`).

## Open
- `hard_rules.md` rule 3 (certificates) stays Weltec-only until **D1** is answered.
- P3/P6 content should be enriched from the Digital Marketing counselling-visit transcripts —
  **register and course content only, never their flow or money behaviour** (those sessions
  quote real fees, correctly for a visit, wrongly for a call).
