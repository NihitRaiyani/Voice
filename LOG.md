# Roma build log (append-only, newest first)

## 2026-07-26 (session wrap) — pre-TTS filter fail-OPEN on Indic fees (FIXED), 5 more verified bugs, Step 5B behind a flag, Step 6 post-call recording

### Goal this session
Three things, in order: Step 5B (barge-in, behind a default-off flag), a guardrail repair
that turned out to be a live gate breach, and Step 6 (post-call recording queue + worker).

### Building now
Nothing in flight. **499 tests pass** (236 at session start), `uv run ruff check src tests
scripts` is clean. Everything is **uncommitted** on `main`.

### The one that mattered — a fee could be spoken on a live call
`filter.py` kept a *private* tokenizer, `re.compile(r"[₹%]|\w+")`. Python's `\w` excludes
Indic combining marks, so it shattered every Devanagari/Gujarati word at each matra
(`फीस` → `['फ','स']`). **Every Indic entry in `lexicon.py` was unreachable**, and

    safe_output("Course ki फीस 25000 hai.")  ->  returned the fee VERBATIM, logged allow:clean

TTS runs `hi-IN` and the LLM is prompted for code-mix, so a money word arriving in
Devanagari is routine, not exotic. CLAUDE.md gate 1 and docs/04, breached, with no error
and no failing test. Fixed by deleting the private tokenizer and delegating to the shared
`normalize.tokens()` (which was promoted precisely to stop this drift), plus a `keep="₹%"`
option because `%` is in `string.punctuation` and would otherwise be stripped away.
`tests/guardrails/test_filter.py` now carries Indic tables — the repo previously had **no**
test containing a Devanagari or Gujarati money token, which is why this shipped.

### Other verified bugs fixed (all found by execution, not inspection)
- `is_affirmation('જી ના')` → **True**. The commonest polite Gujarati refusal read as YES:
  P1 marked the inquiry confirmed and Roma opened discovery on someone who had declined.
  Same detector is the P7 readback fallback, so `ઠીક નથી` could register a booking. Added a
  negation veto. Bare romanized `na` is excluded — it is the tag particle in "theek hai na".
- `OBJECTION_KEYWORDS[COST]` had **no word for "fee" in any script**, so `ફી બહુ વધારે છે`
  classified as `None` and never reached P6 — the one objection the whole flow exists to
  route to a visit. Also demoted three over-broad keywords (`ઘરે`, `online`, `door`) that
  invented objections from ordinary speech (`હું ઘરે જ છું` → ASK_FAMILY), and made phrases
  checked before keywords.
- `timeresolve` booked **3 AM** for "kal teen baje", turned "12 baje raat" into noon, and
  never rejected a slot in the past or a hallucinated `day_offset=-1`. Bare hours 1–7 now
  default to PM; out-of-range returns `None` (re-ask), which is how this module already
  signals every other underdetermined case.
- A `canned.consent_line()` failure produced a **silently dead call**: pipecat swallows
  exceptions out of `on_client_connected`, so no consent line AND no `LLMRunFrame` — Roma
  silent until timeout, teardown logging an ordinary `phase=p1_open won=False`. Now loaded
  once at app build (fail fast), with the handler cancelling loudly if the opening fails.
- Transcript text was logged at INFO (docs/07 PII); dropped to DEBUG.
- `RedactionFilter` shredded every log line when a secret was 1–2 chars long. Minimum
  length guard + a startup warning.

### Step 6 — post-call recording (docs/09)
`src/roma/postcall/` (pipecat-free, so the worker starts without silero/torch):
`paths` `job` `spool` `queue` `store` `worker`, plus `telephony/recorder.py` and
`scripts/run_postcall_worker.py`. Capture is **local** (docs/09 option 2), stereo, ch0 lead
/ ch1 Roma, headerless raw PCM during the call. `finalize_call` was extracted from the
websocket handler's closure so teardown is testable at all.

Verified end-to-end with **no Redis**: teardown → spool → worker → WAV + sidecar, 0600/0700,
capture cleaned up, both legs separable in the stereo file.

### Also
TTS voice `priya` → **`ishita`**, `pace=1.05` (v3 supports pace/temperature; pitch and
loudness are v2-only). Pinned by test — TTS config has regressed on a live call before.

### Decisions made this session
- **`ENABLE_BARGE_IN` defaults False.** No AEC host; the laptop→cloudflared path echoes
  Roma's TTS back into VAD. OFF branch returns the pre-existing object verbatim. → **n**
- **Recording capture = local media capture, not Twilio-side.** Audio never leaves our
  infra; matches Vadodara on-prem + PII locality. Settles the open item in
  docs/decisions.md:58. → docs/decisions.md: **n** (still to add)
- **Enqueue failure spools to a local file.** docs/09 forbids silently dropping a
  recording, but teardown must never fail. `SpoolFallbackQueue.push` never raises. → **n**
- **Storage is hard-gated on consent sign-off.** While `CONSENT_LINE` is the placeholder the
  worker DISCARDS the audio (and deletes the raw capture — leaving it in `media/` forever,
  uncovered by retention, would itself be storing an unconsented recording). → **n**
- **Redis list + `BLMOVE`, not a stream.** Same at-least-once guarantee, far less
  machinery, and fakeredis's list ops are much better exercised than its group emulation.
- **Bare hours 1–7 default to PM; past/out-of-hours slots return None.** → **n**

### Half-done / careful
- **Nothing is committed.** All of the above is working-tree only.
- **`ruff format` has NOT been run** (28 files). Its own commit, not mixed in.
- **No live call this session.** Barge-in has never run on real audio.
- **`LREM` matches by exact string.** `PostcallJob.raw` holds the bytes as pushed and must
  never be re-serialised on ack — otherwise the inflight entry leaks forever and the job is
  redelivered on every restart. Guarded by test; there is a comment at the site.
- **Recordings are gitignored via `var/`.** That entry must stay in step with
  `Settings.roma_data_dir` or the first dev call writes lead PII into the working tree.
- **Adaptive endpointing is only partly effective**, by construction — Sarvam's 1.17s p99
  TTFS often lands after the 850ms timer has fired. Documented in the strategy's docstring;
  docs/10 Step 7 is where the numbers get tuned on real audio.
- **Step 6 = post-call**, per docs/10 (the build-order authority CLAUDE.md points at).
  `HANDOFF.md`, `store.py` and `state.py` all said "Step 6 = lead-import"; corrected.

### Filed, not fixed
- `OutputAudioRawFrame` subclasses `AudioRawFrame`, so `STTService` feeds Roma's own canned
  consent line to Saaras for transcription on every call — wasted spend, spurious
  transcripts.
- `pipecat.pipeline.task` / `runner` are deprecated shims in 1.6.0, slated for removal in
  2.0 (`PipelineWorker` / `WorkerRunner`). Used at `media.py:44-45`.
- The pre-TTS filter's `FEE_CURRENCY` contains `"rs."`, which normalises to `"rs"` — the
  entry is redundant, not a hole (`Rs. 25000` still blocks).

## 2026-07-25 (session wrap) — Step 4 (7-phase routing) + Step 5A (isolation/barge-in readiness), both merged

### Goal this session
Start Step 4 — the 7-phase routing (hardcoded `p1_open` → per-phase switch on conversation state).
Then Step 5, which was split after review of what could actually be verified.

### What shipped
**Step 4 — 7-phase controller (merged `ace1686`).** New `src/roma/controller/`: `state.py` (CallState
per docs/03 + checkpoint round-trip + `as_prompt_vars()` bridge), `machine.py` (PURE `next_phase`,
every docs/03 transition incl. objection cap-2 hard pivot + P7 readback WIN), `objection.py`
(Proposed-7 lexicon), `slots.py` (structured output, the only 2nd model call, gated to P2/P5/P7),
`timeresolve.py` (relative→absolute in code, Asia/Kolkata, conf 0.7), `store.py` (Protocol + InMemory
+ Redis, key `call:{sid}:state`, TTL 4h), `turn.py` (`advance_turn` orchestrator).
`telephony/phase_controller.py` swaps the system message IN PLACE (keeps the persona→hard_rules cache
prefix — NOT `LLMMessagesUpdateFrame`) and pushes `LLMUpdateSettingsFrame(LLMSettings(max_tokens=cap))`,
forwarding the context frame only after. `llm/prompts.py` unchanged — it was already the seam.

**Step 5A — hardening (merged).** Multi-call isolation: the 4 process-global `app.state.last_*` hooks
(clobbered by concurrent calls) replaced by a registry keyed by stream SID, managed by a
`_registered_call` async context manager that removes on ANY exit. Barge-in *readiness* locked by
tests: cancellation routes through the pre-TTS filter, phase controller doesn't advance on
interruption/cancel frames, sanitizer drops half-samples on Cancel/End idempotently.

### Live calls (4, ~18:40–18:55 IST)
- ✅ P1 assembly + speech live-correct (prompt built from `state.phase`, spoken by bulbul:v3).
- 🐞 **Real bug found + fixed:** STT returns Gujarati script, so "haan ji" → `હા જી`; the affirmation
  and objection lexicons had no Gujarati → `inquiry_confirmed` never fired → machine stalled in P1 and
  the model improvised a booking pitch. Fixed + 16 regression tests.
- ⚠️ Could NOT get a clean P2→P7 live run — blocked by dev-env infra, not Step 4: Sarvam STT connect
  failed on 2 of 4 calls; audio lag/no AEC (lead reported choppy audio). Full P1→P7 + win remains
  proven by the offline e2e test.

### Decisions made this session
- Full Step 4 in one pass; Proposed-7 objection taxonomy; call-state keyed by `call_sid` now
  (redial-resume via `lead_id` → Step 6). All user-confirmed. → decisions.md? n (worth adding)
- Pinned where docs were silent: `phase_turn_count` = turns in current phase, reset on entry; P1
  confirm via affirmation lexicon, P5 accept / P7 readback via structured booleans; `locked_slot` set
  only on confirmed readback.
- **Step 5 split (user-confirmed):** 5A = isolation + cancellation-safety (pure software, shipped);
  5B = `enable_interruptions` + backchannel guard + adaptive endpointing, GATED on a clean audio path.
  Rationale: enabling barge-in without a tuned backchannel guard regresses to constant false
  interruptions from echo (no AEC) — the very reason Step 3 disabled it.
- Any lead-facing matcher MUST carry Gujarati spellings (STT is gu-IN). Learned the hard way.

### Session outcome
Steps 4 + 5A both merged to `main` (local, not pushed). **236 tests green.** Code-review run on the
barge-in/concurrency path per the docs/08 mandate; its one finding (isolation tests exercised only the
pure helpers) fixed with real overlapping-coroutine tests. Next task + landmines in HANDOFF.md.

## 2026-07-25 (session wrap) — SESSION.md snapshot: Step-3 garble → v3 Hinglish, live-verified & merged

### Goal this session
Root-cause and fix the still-garbled Step-3 TTS audio, then finish the live proof and merge.

### Building now
**Live call (13:47 IST) reframed the whole bug.** The WAV-header theory did NOT reproduce — the
sanitizer never fired (no RIFF chunk). The real problem the user hears is **language/voice quality**:
poor Hindi, poor Gujarati, jarring code-switching — i.e. `bulbul:v2` renders code-mix badly, and the
persona told Roma to follow leads into Gujarati (with a hi-IN voice → bad switching).

**Fix (Hinglish-primary, user's call):**
- TTS `bulbul:v2` → **`bulbul:v3`** (native Hinglish/code-mix per Sarvam docs), voice `anushka` →
  `priya` (v3 female; tune by ear), still `hi-IN`. `media.py:81-83`, `build_tts`. Builds at 8kHz; 124 green.
- Persona (`prompts/persona.md`): stay in Hinglish throughout; **removed** "follow them into Gujarati."
- STT stays `gu-IN` codemix (still understand Gujarati-speaking leads).
- Reliability note: TTS websocket dropped mid-call once (1011 keepalive ping timeout, auto-reconnected)
  — separate issue, not yet addressed.
- The WAV-header sanitizer stays as harmless defensive code (no-op unless a RIFF chunk ever appears);
  it was NOT the fix.

**LIVE-VERIFIED (14:03 IST):** v3 + priya + Hinglish persona — Hindi sounds natural, switching gone,
no keepalive drop. User approved → Step 3 merged to `main`.

### Decisions made this session
- Fix as a decoupled `FrameProcessor`, not a subclass override of pipecat's private `_receive_messages`
  (CLAUDE.md: Pipecat services API changes fast). → not yet in docs/decisions.md.
- Language strategy = **Hinglish-primary** (Roma speaks Hindi+English; does not follow leads into Gujarati).

### Session outcome
All "finish the live proof and merge" items completed: v3 Hinglish live-verified and merged to `main`
(`14316fe`), pushed. 124 tests green. Sanitizer kept as a defensive no-op (keep/revert still open).

## 2026-07-25 — Live call reframes the bug: it's v2 code-mix QUALITY, not byte-garble → v3 + Hinglish persona
First live call of the WAV-header fix (13:47 IST, 2973 inbound frames, full turn worked: STT codemix
gu-IN, LLM gpt-4o-mini, TTS). **The WAV-header theory did NOT reproduce** — the sanitizer never fired
(no RIFF chunk), so the earlier root cause was a wrong track. The user's actual complaint: "Hindi very
poor, Gujarati not good, switching bad." Root cause is **language/voice rendering**: `bulbul:v2` handles
code-mix badly, and the persona told Roma to follow leads into Gujarati while the TTS voice is hi-IN →
jarring switches. Current Sarvam docs (context7): **`bulbul:v3` natively handles code-mix (Hinglish)**;
v2 is legacy.
- **Fix (user chose Hinglish-primary):** TTS `bulbul:v2`→`bulbul:v3`, voice `anushka`→`priya` (v3 female),
  still `hi-IN` (`media.py:81-83`). Persona (`prompts/persona.md`): stay in Hinglish, removed "follow them
  into Gujarati." STT stays `gu-IN` codemix (still understand Gujarati leads). Builds at 8kHz; 124 green.
- **Reliability:** TTS websocket dropped once mid-call (1011 keepalive ping timeout, auto-reconnected) —
  logged, not yet fixed.
- **Sanitizer status:** KEPT as a harmless defensive no-op (strips a RIFF header IF one ever appears);
  it was not the fix but guards a real pipecat WS/HTTP asymmetry, is unit-tested, and the v3 call proved
  it doesn't harm good audio.
- **LIVE-VERIFIED (14:03 IST):** second call on `bulbul:v3` + `priya` + Hinglish persona — config confirmed
  on the wire (`model=bulbul:v3 speaker=priya hi-IN 8000`), Hindi sounds natural, switching gone, no
  keepalive drop. User approved. Step 3 audio is DONE → merging `step3-llm-filter-tts` to `main`.

## 2026-07-25 — Garbled-audio bug ROOT-CAUSED + fixed (WAV header on Sarvam WS stream); live-proof deferred
Root-caused the garble to the streamed-TTS path, confirmed by two independent sources:
- Sarvam's own docs (context7): the linear16 **streaming websocket is WAV-framed** — their example
  writes the concatenated base64 chunks straight to `output.wav`, which only works if the opening
  chunk carries a RIFF header.
- Pipecat 1.6 code asymmetry: the WS receive path (`SarvamTTSService._receive_messages`,
  `.venv/...sarvam/tts.py:1154-1155`) wraps each chunk directly as a `TTSAudioRawFrame` with **no RIFF
  strip and no sample alignment**, whereas its HTTP path (`run_tts`, `:636-639`) does
  `if audio.startswith(b"RIFF"): audio = audio[44:]`. The 44 header bytes land in the output PCM16
  buffer and byte-shift every following sample → robotic garble. The canned single frame and
  Bulbul-in-isolation never hit this path, which is why they were clean.

**Fix:** `src/roma/telephony/tts.py` → `TTSAudioSanitizer` (FrameProcessor) between `tts` and
`transport.output()` — strips a leading WAV header from any `b"RIFF"` chunk and holds PCM16 even-byte
alignment across chunks (carry). Decoupled from pipecat's private `_receive_messages` (chosen over a
subclass override; CLAUDE.md: services API churns). No-ops safely on already-clean/even chunks.
`ROMA_CAPTURE_TTS=1` dumps raw pre-sanitize PCM to a wav. Also committed the prior session's uncommitted
interruption fix first (`c8dcf59`). New tests `test_media_tts.py` (5); **suite 123 green**.
Commits: `c8dcf59` (interruptions), `b803511` (docs), `c496e48` (WAV fix + tests).

**Deferred by decision:** the live Twilio call to confirm clean audio by ear — code-complete +
unit-tested + doubly-evidenced, but not yet live-proven. Owed before real calls.

## 2026-07-25 — Step 3 built (LLM + filter + Bulbul); turn works live; garbled-audio bug UNRESOLVED
Built Step 3 on `step3-llm-filter-tts` (commit `d2b5013`, offline part): `user_agg →
OpenAILLM(gpt-4o-mini) → SentenceAggregator → PreTTSFilter(safe_output) →
SarvamTTS(bulbul:v2, anushka, hi-IN) → output → assistant_agg`. New `llm/prompts.py`
(persona→hard_rules→P1 assembly, docs/11) and `telephony/pretts.py` (routes every LLM
sentence through the guardrail). 118 tests green; verify_media PASS.
- **Live proof (worked):** Roma replies via LLM→filter→Bulbul, on-persona (greets, books a
  visit, ends on a question), `response.usage` logged per turn, OpenAI prompt caching engaged
  (`cached=1024+`), full turn on real uvicorn. Fee-filter live turn skipped — unit tests
  (`test_pretts`) cover the four categories + CERT.
- **BUG — garbled/choppy/robotic audio (STILL OPEN):** systematic-debugged. Bulbul output in
  ISOLATION is clean (1 frame, 8000 Hz, correct duration) → not codec/rate. Found **12 spurious
  interruptions** (Pipecat default `enable_interruptions=True` on the user-turn start strategies)
  cancelling Roma's TTS mid-sentence. Fixed via `build_user_params()` (`enable_interruptions=
  False`, keeps default smart-turn endpointing) + a test. Retest: **0 interruptions, 1 stable TTS
  socket (was 10)** — but the user still reports the audio is bad. So interruptions were a real
  bug but **NOT the root cause of the garble. Root cause still unfound.**
- **Notes:** Pipecat 1.6 default endpointing is **smart-turn** (`LocalSmartTurnAnalyzerV3`), not
  the Step-2 `stop_secs=0.85` (that only governs VAD speech-stop). A one-off calling-window
  override (spoofed noon `now`) was used to test after 21:00 IST — never committed.
- **Uncommitted in the working tree:** the interruption fix (`media.py` + `test_media_stt.py`).

## 2026-07-24 — Step 1 live-proven & merged; Step 2 (VAD+STT) built, live-proven & merged; Step 3 planned
Live-verified Step 1 end-to-end and built + live-proved Step 2, both merged to `main`
(`90ac75d` → `e64ab22`). Then pulled context7 and got an approved plan for Step 3.

- **Step 1 live:** first real Twilio trial call proved audio IN (1018 inbound frames /
  325,760 PCM bytes) + OUT (canned tones heard after the trial preamble). Added
  `scripts/place_test_call.py` (place-call driver) + `scripts/serve_media.py` (logging-wired
  test server with `/debug/last-counter`; `auto_hang_up=False`). **RTT deferred** — no
  Vadodara host to SSH into, so the docs/02 ~100ms edge RTT can't be measured yet.
- **Step 2 live:** Silero VAD + Sarvam Saaras STT wired into `/ws`
  (`input→counter→stt→transcript→output`). Real transcripts on 8kHz: `'Hello'`,
  `'Hello કોઈ બોલ રહ્યું છે?'`, `'હું પછી વાત કર શકતો હું hello.'` — Saaras **codemix**
  confirmed (English + Gujarati script), `gu-IN`, **processing latency median ~154ms**
  (81–279ms normal turns; within docs/02 budget). New `transcript.py` (TranscriptionLogger),
  `pipecat-ai[sarvam,silero]`, 110 tests green.
- **Decisions:** D1 = fixed Silero `stop_secs=0.85` (smart-turn `LocalSmartTurnAnalyzerV3`
  deferred to Step 4/5). D2 = `SarvamSTTService` exposes `mode="codemix"` (installed API).
  D3 = `language=gu-IN` primary (WER may revisit). Sarvam `model=`/`params=` kwargs deprecated
  → switched to `settings=SarvamSTTService.Settings(...)`.
- **Engineering finding:** the 3-async-processor pipeline (counter+STT+transcript) **deadlocks
  Starlette TestClient's sync portal at teardown** — runs fine on real uvicorn (proven live).
  Handled: VAD/STT injectable factories; `verify_media` reads mid-session + `os._exit`; offline
  component tests use Pipecat `run_test`, not the full websocket app.
- **Step 3 planned + approved** (`~/.claude/plans/work-on-place-the-quirky-steele.md`): LLM +
  pre-TTS filter + Bulbul TTS, scope 3a (full turn), caches deferred. Confirmed installed APIs:
  `OpenAILLMService`, `LLMContext`/`LLMContextAggregatorPair`, `SentenceAggregator`,
  `SarvamTTSService` (bulbul:v2, persistent socket, 8kHz). Not yet built.
- **Still open:** scored Saaras WER test (non-blocking); RTT on the Vadodara host (no host);
  ₹200 OpenAI dashboard hard cap must be set before Step-3 live calls.

## 2026-07-24 — Step 1 committed + security-review hardening
Landed Step 1 on branch `step1-telephony-spine` (3 commits, unpushed) and addressed the
background commit security review. Twilio confirmed to run on **free/trial credits**.
- **Commits:** `b17ce79` feat (telephony spine), `b9546c0` docs (session close),
  `96cc554` harden. Branch unmerged/unpushed — merges after the live call verifies.
- **Security review (3 findings on `media.py`, addressed):** (1) **auth** — `/ws` now rejects
  connections whose `start.accountSid` ≠ our configured Twilio SID (close 1008; SID values never
  logged); shared-secret `<Parameter>` origin proof deferred to Step 5 (docs/08). (2)
  **input-validation** — `_read_start` bounds pre-`start` reads (DoS guard), tolerates non-JSON,
  rejects a `start` missing `streamSid` instead of KeyError-crashing. (3) **observability** —
  per-call inbound frame/byte counts logged at teardown; `app.state.last_counter` marked honestly
  as a single-call test hook (multi-call isolation = Step 5).
- **Tests:** 100 → **106 green** (+6: bounded-prelude, non-JSON, missing-streamSid, account-SID
  rejection via TestClient). Synthetic e2e still PASS.
- **Twilio trial reality (saved to memory):** outbound reaches **Verified Caller IDs only**; a
  trial preamble plays before the stream (consent/greeting follow it); limited credit. Folded into
  the HANDOFF runbook.
- **Caught + reverted:** an external edit had deleted ~40 lines of locked decisions from
  `docs/decisions.md` (filter, pre-dial gate, prompt-caching order, transcript provenance) and
  re-added `FRONTEND_PORT`/`BACKEND_PORT` — restored the file; kept it out of every commit.

## 2026-07-24 — Step 1: telephony spine (code-complete, live call pending)
Twilio outbound + Media Streams → bare Pipecat transport, audio in/out with a canned line behind
the filter, on the Gate-0 pre-dial gate. FF-merged `gate0-pretts-filter` → `main` (pushed) first;
Step 1 on branch `step1-telephony-spine` (unmerged).
- **Package `src/roma/telephony/`:** `twiml.py` (`<Connect><Stream>` — bidirectional, not
  `<Start><Stream>`), `dialer.py` (`place_call` gated by `precall_check`, dials only on
  `may_dial=True`, injected Twilio client, `build_twilio_client()` reads secrets at boundary),
  `media.py` (FastAPI `/ws`: reads Twilio `connected`/`start`, builds `TwilioFrameSerializer` +
  `FastAPIWebsocketTransport` into `input → InboundAudioCounter → output`, speaks consent + canned
  greeting on connect), `canned.py` (loads μ-law assets, enforces `safe_output()` on text at
  load), `ulaw.py` (pure-Python G.711 — stdlib `audioop` gone in Py3.13).
- **Scripts:** `make_canned_clip.py` (WAV→μ-law + placeholder gen), `rtt_mumbai.py` (TCP-handshake
  RTT to Twilio edge), `verify_media.py` (synthetic Media Streams e2e).
- **API facts (context7):** pipecat **1.6.0** paths `pipecat.transports.websocket.fastapi` +
  `pipecat.serializers.twilio`; Twilio bidirectional needs `<Connect><Stream>`.
- **Config:** `public_wss_base` added to `Settings` + `.env.example`. Removed stray
  `FRONTEND_PORT`/`BACKEND_PORT` (broke the sync-test invariant); dev ports 3020/8020 live in
  agent memory, not `.env.example`.
- **Verify:** `uv run pytest -q` → **100 passed** (+19 telephony). `scripts/verify_media.py` →
  PASS (audio-IN 5 frames counted, audio-OUT 5 media frames through the real transport). No live
  Twilio call this session (no creds/number/public endpoint).
- **Careful:** `assets/{consent,demo}.ulaw` are TONE placeholders; `CONSENT_LINE` placeholder
  wording (Weltec-owned). Test numbers only. `auto_hang_up=True` (prod) hits Twilio REST on end.
  Branch unmerged.

## 2026-07-24 — Gate 0 complete: pre-dial gate (consent + DND/window)
Built Gate 0's third bullet — the last item before a callable build. Gate 0 is now DONE.
- **Package `src/roma/dialer/`** (`consent → window → dnd → precall`), the mirror of the pre-TTS
  filter for the dialer path. `precall_check` is the gate the Step-1 dialer must call; dials only
  on `may_dial=True`, speaks `consent_line` first. Committed `391bb55` on branch
  `gate0-pretts-filter` (still unmerged). 81 tests green (65 prior + 16 new `tests/dialer/`).
- **Decisions:** DND posture = **allowlist-only** (unknown numbers blocked, never dial unguarded);
  consent line = **placeholder constant** pending Weltec compliance sign-off; calling window =
  **09:00–21:00 IST** module constants (confirm exact TRAI window w/ Weltec). All in decisions.md.
- **Careful:** `StubRegistry` is NOT the TRAI registry and `CONSENT_LINE` is a placeholder — both
  Weltec-owned follow-ups (decisions.md Open). No Twilio API yet (Step 1). Branch unmerged.

## 2026-07-24 — Gate 0 filter wrap + prompt system scaffolded
Closed the pre-TTS filter task and added the prompt system as data.
- **Filter (branch `gate0-pretts-filter`, unmerged):** 65 tests green; enforced skill
  `.claude/skills/roma-guardrail/SKILL.md` created from `docs/04` (skill-creator step —
  encodes verified behavior, points at `lexicon.py`).
- **Prompts:** `docs/11-prompts.md` + `src/roma/prompts/` — persona.md, hard_rules.md,
  phases/p1_open…p7_close.md. Runtime data, assembled persona → hard_rules → phase →
  call-state (order load-bearing for prompt caching). Relocated the 7 phase files out of a
  stray root-level `phases/` into `src/roma/prompts/phases/`. Loader/assembler not built (Step 3/4).
- **decisions.md:** restored 4 Gate-0 bullets a stale paste had deleted; added 2 entries —
  prompts-as-runtime-data, and source-transcript provenance (the DM counselling transcripts are
  the VISIT not Roma's call: content/register/negative-filter-tests only, never call flow or
  money, because they quote fees correctly for a visit and catastrophically for a call).
- **Doc audit:** read all 24 `.md`. Flagged: README still says "spec, not code"; `decisions.md`
  Open still lists "OpenAI model per turn" though Locked now says GPT-4o-mini; stale tracked
  duplicate `mnt/user-data/outputs/roma/skills/README.md`; and Gate 0's third bullet (consent +
  DND/calling-window stub) is still unbuilt — the one next task.
- **Landmine:** most of the above is uncommitted (branch unmerged; several docs modified;
  `docs/11` + `src/roma/prompts/` untracked).

---

## 2026-07-20 — Gate 0 second half: pre-TTS guardrail filter
Brainstormed → approved spec → plan → TDD execution (feature branch
`gate0-pretts-filter`, 5 atomic commits). `src/roma/guardrails/`: `normalize.py`,
`lexicon.py` (the enforced config), `filter.py` (`screen`/`safe_output`). Four block
categories (fee/salary/placement/discount) + CERT, negation-gated pushback allow-case
(cue within 3 tokens of a signal; clauses split on `, ; ।` and spaced dash, never an
unspaced hyphen), per-category substitution lines, permitted-EMI allowlist, fail-safe
hard-fail line. Full spec test table green — block / allow / pushback / negation-distance
/ hyphen / token-boundary / prior-quote injection / fail-safe. Stdlib only, no new deps.
47 guardrails tests + 18 scaffold = 65 green. Spec/plan under `docs/superpowers/`.
Next: skill-creator (turn `docs/04` into an enforced skill), then STOP before Step 1.

---

## 2026-07-20 — Gate 0 first half: secret & security scaffold
Bootstrapped the repo's first code. Initialized session-continuity files. Brainstormed →
approved design → formal plan → subagent-driven execution (5 tasks, TDD) → independent code
review.

- Stack: `uv` + `pyproject.toml`, `src/roma/` layout, pydantic-settings 2.14.2, pytest.
- `src/roma/config.py`: `Settings(BaseSettings)`, 6 required `SecretStr` credentials + app_env/
  log_level; `get_settings()` (`lru_cache`) fail-fast; `env_ignore_empty=True` so blank keys
  also fail (fixed a review HIGH).
- `src/roma/logging_setup.py`: `RedactionFilter` scrubs secrets from message, args, tracebacks,
  and stack_info (traceback scrubbing added to fix a review HIGH); `configure_logging()`.
- `.env.example` values-free + bidirectional drift-guard test vs `Settings` fields.
- 18 tests green. Verified: empty key → ValidationError; secret never in repr; no `.env` tracked.
- Decisions (locked in design doc, not yet in docs/decisions.md): pydantic-settings over
  hand-rolled env; uv over venv; env-only secrets (no external vault) for self-hosted Vadodara.
- git initialized, merged to `main`, pushed to GitHub NihitRaiyani/Voice_Agent.

---
(previous entries below)
