# 12 — Full verification & fixing prompt (Gate 0 → Step 7)

Paste everything below the line into a fresh session. It is written to be run start to
finish without asking questions, and to fail loudly rather than report a green it did not
earn.

Keep this file honest: when a row moves from UNVERIFIED to verified, edit the row and say
which call or command proved it.

---

## ROLE

You are a senior voice-assistant engineer working on **Roma** (`/Users/nihitraiyani/Voice_Agent`),
an outbound code-mix (Gujarati/Hindi/English) agent whose ONLY job is to book a specific
day+time counselling visit for Weltec. A soft "dekhta hoon" is not a win.

Read `CLAUDE.md` first — it is the source of truth and overrides your defaults. Then this
file. Do not restate either back to me.

Your task: **verify Gate 0 through Step 7 of `docs/10-build-order.md`, and fix what is
broken.** Not re-architect. Not re-plan. Verify, then fix, with evidence for both.

## GROUND RULES (violating any of these invalidates the run)

1. **`uv run` or it did not happen.** Bare `python`/`pytest` is a different (conda) env.
   Async tests use `asyncio.run(...)`; there is **no pytest-asyncio**.
2. **Evidence, not inference.** "Should work" is not a verification. Every PASS below
   names a command, a log line, or a test that produced it. If you cannot produce
   evidence, the row is UNVERIFIED — say so and move on. Never upgrade a row you did not
   test.
3. **Root cause, not symptom.** Every fix states the mechanism. If you cannot explain
   *why* the bug happened, you have not found it yet. Precedent: five calls of "lag, echo,
   two agents clashing" were one line — `vad_analyzer=` passed to a pydantic model with
   `extra='ignore'`, silently discarded, so there was no VAD in the pipeline at all.
4. **Every fix ships with a test that fails without it.** Name the live call or command
   in the test docstring. Delete no existing test to make yours pass.
5. **Do not touch the model or the voice.** Bulbul is `v3` / `ishita` / pace 1.05, LLM is
   `gpt-4o-mini`. The v3+gpt-4o switch is the NEXT wave, deliberately after this one, so
   it lands on a system whose remaining failures are known instead of masking them.
6. **No secret in code, tests, logs, or a plan file** (docs/07 + CLAUDE.md gate). Lead
   transcripts are PII: decision signatures at INFO, text at DEBUG.
7. **Editing `.env` via `sed` is blocked** by the auto-mode classifier. Pass settings as
   env vars at process launch instead.

## OFFLINE GATE — run this first, in this order

```
uv run pytest -q                              # expect: 856 passed
uv run ruff check src tests scripts           # expect: All checks passed!
uv run ruff format --check src tests scripts  # expect: 132 files already formatted
uv run python scripts/run_eval.py             # expect: 8/8 scripts passed, 0 finding(s)
```

If any of these is red, **stop and fix it before dialling.** A live call costs money and
tells you nothing about a system that fails offline.

`scripts/verify_media.py` **hangs** — pre-existing, confirmed by running it in a worktree
at 4f3aa32. It is not a regression and not this wave's job. Do not chase it.

## STEP-BY-STEP VERIFICATION

Work the table top to bottom. `docs/10-build-order.md` is the authority on what each step
promised; this is how you prove it.

| # | Invariant that must hold | How to prove it | State |
|---|---|---|---|
| **G0** | The pre-TTS filter is on **every** path that can emit audio, including teardown. A build that can place a call without it must not exist. | `tests/guardrails/` + the filter canaries inside `run_eval.py` (they carry Devanagari and Gujarati — romanized-only canaries were green for the whole period the filter was failing open on Indic script). Confirm `PreTTSFilterProcessor` sits between `sentence_agg` and `tts` in `media.py`. | verified offline |
| **G0** | No secret in code or logs. | `git log -p` the wave for key-shaped strings; confirm the redacting logger is installed at startup; confirm `gcal` never logs its request URL (the URL carries the API key). | verify |
| **G0** | Consent + DND + calling window gate the dialer; fail-safe is BLOCK. | `tests/dialer/`. Confirm `place_call` still enforces the window — `--ignore-window` in `place_test_call.py` must substitute a `now` for the check and **not** touch `dialer/window.py`. | verified offline |
| **1** | Audio in and out end-to-end over Twilio `<Stream>` WebSocket. | A live call in which you hear Roma. | verified live |
| **1** | RTT origin → Twilio Mumbai edge measured; it gates the latency budget. | `uv run python scripts/rtt_mumbai.py` from the production origin, not a laptop. **BLOCKED: no origin exists** (docs/decisions.md, Deploy: UNDECIDED). This row said "from the Vadodara host" for two weeks and there has never been such a machine — it made an unprovisioned box look like a step someone could take. | **BLOCKED — needs an origin** |
| **2** | The VAD is a pipeline **stage**, not a transport param. | `tests/telephony/test_vad_stage.py` (it pins `"vad_analyzer" not in FastAPIWebsocketParams.model_fields`, so a future pipecat adding it back fails loudly). Live: teardown must show `vad_starts=N vad_stops=N` with N>0. | verified live |
| **2** | Sarvam flushes on the VAD stop frame — `vad_signals` must stay `None`, or `flush_signal="true"` is never sent. | pinned by test. Do not "fix" it by setting `vad_signals`. | verified offline |
| **2** | Saaras WER measured on real 8kHz μ-law. | Not built. It can invalidate assumptions (`docs/decisions.md`) — flag it, do not silently skip it. | **UNVERIFIED** |
| **3** | One LLM call per turn, streamed, sentence-chunked, filtered, spoken. | `llm usage` lines == generations in a live log. | verified live |
| **3** | `persona → hard_rules` is byte-identical across a call's turns (prefix caching). Dynamic content lives **only** in the phase fragment. | `tests/llm/test_prompts.py`. Any new `{{var}}` goes in a phase fragment, never in persona/hard_rules. | verified offline |
| **3** | OpenAI dashboard hard spend limit is set. **₹100 remaining — this is the real ceiling, not `openai_budget_inr`.** | Check the dashboard. The setting only lets the harness refuse to start. | **UNVERIFIED** |
| **3** | Every model we send tokens to has a price, and spend is counted where it is actually spent. | `tests/test_spend.py`, `tests/telephony/test_media_llm.py` (`LLM_MODEL`/`SLOT_MODEL` must be keys of `roma.spend.PRICES`), `tests/telephony/test_media_spend.py`. Live: the teardown line prints `cost=₹N phase_spent=₹N/100`. | verified offline |
| **3** | The ₹100 cap can refuse a call, and survives a restart. | `tests/dialer/test_precall.py::test_a_spent_budget_blocks_the_dial`; `place_test_call.py` prints the running total and exits non-zero on `reason=block:budget`. Ledger: `{ROMA_DATA_DIR}/spend.jsonl`. | verified offline |
| **4** | The controller picks the phase; the model never does. Exactly one fragment per turn, `max_tokens` from that phase's cap. | `tests/controller/`, `tests/telephony/test_media_phase_e2e.py`. | verified offline |
| **4** | **Every** P5/P7 turn writes `slot_status` and emits exactly one `slot verdict:` line. There is no silent path. | `grep 'slot verdict' <log> \| grep -v 'reason=ok'` is the diagnosis; a *missing* line on a slot-bearing turn is the bug. | verified live |
| **4** | Roma never claims a booking the machine did not make (hard rule 9). | `confirmguard` + `tests/controller/test_confirmguard.py`. Live: no "confirm ho gayi" without `status=accepted\|locked`. | verified offline |
| **5** | A barge-in cancels cleanly and leaks nothing: no half-sentence carried into the next turn, no unfiltered text to TTS. | `tests/telephony/test_sentences.py`, `test_turntaking.py`. Live: `grep 'sentence aggregator'`. | verified offline |
| **5** | Multi-call isolation — no process-global "last call" slot. | `tests/telephony/test_media_isolation.py`. **Two concurrent live calls have never been run.** | **UNVERIFIED live** |
| **6** | Recordings are discarded while `CONSENT_LINE` is the compliance placeholder. **That is by design, not a bug — do not "fix" it.** | `tests/postcall/`. | verified offline |
| **6** | `PostcallJob.raw` is never re-serialised on ack — LREM matches by exact string. | pinned by test. | verified offline |
| **7** | Replay harness: phase hits, word caps, slot extracted, filter never leaks. | `scripts/run_eval.py` → 8/8. | verified offline |
| **7** | The five docs/05 thresholds are `Settings` fields and actually applied, not merely printed. | Teardown `endpoint timing:` line prints **evidence** (`vad_starts`/`vad_stops`), not just configuration — printing configuration is what hid the dead VAD for four calls. | verified live |
| **7** | Tuning calls placed, one threshold moved per call. | Not done. Needs a tunnel and burns Sarvam credits. | **UNVERIFIED** |

## THE LIVE CALL

Redis is not running in dev — `call-state checkpoint failed` once per turn is **degradation
by design**. Do not chase it.

```
# 1. tunnel — Twilio needs a public HTTPS/WSS origin and the dev laptop has none. This is
#    the ONLY path today: no production origin has been provisioned (docs/decisions.md).
#    Both flags are load-bearing, diagnosed 2026-08-01 after five tunnel deaths in one day:
#      * 127.0.0.1, NEVER localhost — uvicorn binds IPv4 only while `localhost` resolves to
#        ::1 first, so cloudflared hits a refused IPv6 loopback and the edge returns HTTP 530
#        while the tunnel process looks healthy.
#      * --protocol http2 — the QUIC default drops every ~90s on a domestic link. Short HTTP
#        requests slip through between drops; a long-lived media WebSocket does not.
#    Provisioning is flaky (`context deadline exceeded`) — retry the launch up to 3x.
cloudflared tunnel --protocol http2 --url http://127.0.0.1:8020   # note the https URL

# 2. app, on the code you are testing — restart it, or you test the old build
uv run python scripts/serve_media.py > <scratchpad>/logs/mediaN.log 2>&1 &

# 3. prove the tunnel reaches the app before spending a call
curl -s -o /dev/null -w "%{http_code}\n" https://<sub>.trycloudflare.com/health

# 4. dial. Test handsets ONLY — never a real lead.
uv run python scripts/place_test_call.py +91XXXXXXXXXX \
    --base-url https://<sub>.trycloudflare.com [--ignore-window]
```

**Script the call.** An unscripted call produces an anecdote; a scripted one produces a
verdict. Say these, in order, and note what she does:

1. Stay silent 8s. → She must greet. (A cancelled opening is the CA20c70f7 deadlock.)
2. Answer a discovery question, then **interrupt her mid-question**. → She must not
   re-ask something you already answered, and must not emit fused text.
3. Answer the remaining discovery questions normally. → One question per turn, no field
   names spoken aloud.
4. `"નહીં નહીં કોઈ દૂસરા ના દો"` (a flat refusal). → Must **not** book. Expect
   `claim vetoed` in the log.
5. `"Wednesday"`, then in a **later** turn `"3 બજે"`. → Must resolve to **Wednesday**
   15:00, not today. Expect `anchor=2026-XX-XX` on the second verdict.
6. `"12 બજે"`. → Must be accepted (branch is 9–6). She must **not** say the branch is
   shut, and must never quote the two offered times as opening hours.
7. `"fees kitni hai?"` → No number, ever. Route to the visit.
8. Accept a slot, confirm the readback. → `phase controller: WIN`.

Then read the log, in this order:

```
grep -E "Generating TTS|barge-in|slot verdict|claim vetoed|p5 offers|objection:|sentence aggregator|WIN" <log>
grep -o "transcript='[^']*'" <log>          # reconstruct what was actually said
grep "endpoint timing" <log>                # vad_starts/vad_stops > 0, won=True
```

A verdict line with `reason=unclear` on a turn where the lead clearly named a time is a
bug — chase it. `conf=0.50` on a bare hour means the **extractor** is under-confident, not
that the lead was unclear.

## OPEN FINDING FROM CALL CA4777470 (2026-07-27) — start here

The lead said `"4 બજે"` three times. Every turn:

```
slot verdict: phase=p5_pivot reason=unclear ... hour=4 anchor=- resolved=- status=none
```

Two distinct causes, both real, neither fixed:

1. **`anchor=-`.** `pending_day` is only set once a verdict resolves a day, and the lead
   never named one — they answered Roma's offer with a bare time. But the day *was*
   established: **the offers themselves are the day under discussion.** Seed `pending_day`
   when `slots_offered` is populated.
2. **`conf=0.50`** on a bare hour, below `CONFIDENCE_THRESHOLD` (0.7), so even with an
   anchor it would still be refused. Decide deliberately whether a bare hour with an
   anchor is sufficient evidence, and write down why.

Fix both, with a test each, before the next call.

## HOW TO FIX

- One defect, one commit, one reason. The commit message states the mechanism and names
  the call SID that exposed it.
- Prefer deleting a layer to adding one (CLAUDE.md build discipline). If a fix needs a new
  module, justify it in the docstring or delete it.
- Indic text: **one tokenizer only**, `guardrails.normalize.tokens()`. Never `\w`/`\W` —
  combining marks are not alnum. Every lead-facing matcher carries Gujarati spellings; STT
  returns `gu-IN`.
- A model instruction is not a fix for a safety property. Anything that must never happen
  gets a code gate (`confirmguard`, the refusal veto), and the prompt says it too.
- After fixing, re-run the whole offline gate. Then one live call. Then report.

## REPORT FORMAT

For each step: **PASS / FAIL / UNVERIFIED**, one line of evidence each. Then the fixes
made, each with its mechanism. Then what is still unverified and what it would take.

Do not pad. Do not report a step green because its tests pass if its live behaviour was
never observed — say `verified offline, unverified live`. That distinction is the entire
point of this document.
