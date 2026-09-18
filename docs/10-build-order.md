# 10 — Build Order

Build in this order. The early steps are gates: do not proceed past one that isn't done.
Do NOT start with the LLM or the "fun" parts.

## Gate 0 — Security & guardrail scaffold (before anything callable)
- Secret store + env wiring (`docs/07`). No keys in code.
- Pre-TTS filter as a standalone, unit-tested module (`docs/04`) — with the four block
  categories, the allow-case, the substitution lines, and the fail-safe.
- Consent line + DND/calling-window check stubbed into the dialer path. **[DONE]** —
  `src/roma/dialer/` (`precall_check` gate: allowlist-only DND, 09:00–21:00 IST window,
  placeholder consent line, fail-safe BLOCK). The Step-1 dialer MUST call it and dial only on
  `may_dial=True`. Twilio API wiring is deferred to Step 1 (pull context7 Twilio docs there).
**A build that can place a call without the filter is not allowed to exist.**

## Step 1 — Telephony spine
- Twilio number + outbound call + signed bidirectional Media Stream into Pipecat.
- Verify RTT Vadodara → Twilio Mumbai edge here (it gates the latency budget).
- Prove audio in/out end-to-end with a hardcoded canned line (still behind the filter).

## Step 2 — STT + VAD in
- Silero VAD (`docs/05`), Saaras STT streaming, endpoint-on-final at 850ms default.
- Verify transcripts arrive; measure real Saaras behavior on 8kHz (feeds the WER test).

## Step 3 — LLM + filter + TTS out
- OpenAI streaming → sentence chunk → **filter** → Bulbul **v2** (persistent socket).
- **Prompt loading (`docs/11`):** load `src/roma/prompts/persona.md` + `hard_rules.md` at
  startup. Assemble in the fixed order persona → hard_rules → (phase) → call-state — that order
  is load-bearing for prompt caching, do not reorder. A single hardcoded phase is fine here;
  the per-phase switch lands in Step 4.
- Filler-token cache + fixed-phrase TTS cache (`docs/06`).
- **Set the OpenAI dashboard hard spend limit (₹100)** and log `response.usage` per call from
  this step on — the ₹100 testing cap must be enforced before real call volume (`docs/02`).
- Now you have a full turn. Test the filter catches the four categories on real output.

## Step 4 — Phase machine
- The 7-phase controller (`docs/03`) + call-state in Redis + resume-on-drop.
- **Wire the phase fragments (`docs/11`):** controller loads exactly ONE of
  `src/roma/prompts/phases/p1_open.md … p7_close.md` per turn, and sets `max_tokens` from that
  phase's word cap. The controller picks the phase; the model never does.
- Slot extraction via structured output; readback-confirm loop (the win condition).

## Step 5 — Barge-in & concurrency hardening
- Full interruption lifecycle (`docs/05` L3) + cancellation-through-filter.
- Multi-call isolation (`docs/08`). Run code-review on this path.

## Step 6 — Post-call
- Queue + worker: store the recording (`docs/09`). CRM/calendar write async (later).

## Step 7 — Eval & tuning
- Replay harness against the transcripts: phase hits, word caps, slot extracted, **filter
  never leaks**. **[DONE]** — `src/roma/eval/` + `scripts/run_eval.py`, corpus in
  `evals/scripts/`. Offline is the default and makes zero network calls; `--live` replays
  the same scripts through the real model, metered against `Settings.openai_budget_inr`.
  - It replays **fixtures, not captured calls**: nothing persists a transcript today
    (`PostcallJob` deliberately carries none — it is lead PII). Real-call replay lands with
    consent sign-off, which is the same gate that makes the post-call worker discard
    recordings today. A fixture also states its EXPECTATION, which a captured transcript
    cannot — that is what makes it a regression suite rather than a log.
  - The filter canaries carry Devanagari and Gujarati spellings and run unconditionally.
    Romanized-only canaries would have been green for the whole period the pre-TTS filter
    was failing open on Indic script.
- Tuning scaffold **[DONE]** — docs/05's five thresholds are `Settings` fields
  (`VAD_STOP_SECS`, `ENDPOINT_*`, `BACKCHANNEL_MAX_SECS`), so re-tuning on the production origin
  is an env change rather than a code edit, and teardown logs an `endpoint timing:` line
  carrying the knobs in effect plus the measured VAD-stop → final-transcript latency.
  That latency is the half of turn-final delay the 850ms knob CANNOT fix; measure it before
  moving anything, or the tuning session optimizes the wrong number.
- **Still to do:** place the tuning calls. Needs a cloudflared tunnel and burns Sarvam
  credits; Sarvam STT connect is flaky (2 of 4 calls failed). Move ONE threshold per call.

## Runs alongside (not blocking the build)
- **Saaras WER test** (`docs/decisions.md`) — run early, it can invalidate assumptions.
- **D1 cert answer** from Weltec — unblocks the cert line in the filter.
