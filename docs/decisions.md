# Decisions & Open Items

## Locked
- **Stack:** Vobiz + Pipecat + Silero + Saaras + OpenAI + Bulbul + Redis. (`CLAUDE.md`)
- **Telephony: Twilio -> Vobiz, WebSocket `<Stream>` (2026-07-31).** The stack lock above
  required a written justification to change; this is it.

  *Why Vobiz at all:* operator decision (Indian carrier, TRAI compliance, INR billing).

  *Why the WebSocket transport and not the SIP trunk:* Vobiz offers both, and its own docs
  put Pipecat under the WebSocket `<Stream>` pattern — SIP is the path for platforms that
  expose a SIP URI (Vapi, Retell, LiveKit). Pipecat 1.6.0 has no SIP transport, no RTP
  transport, no SDP outside aiortc's WebRTC, and zero occurrences of `INVITE`; its entire
  telephony surface is six WebSocket vendor serializers plus Daily's SIP-as-a-service. Taking
  the SIP path would have meant running Asterisk or FreeSWITCH as a SIP->WebSocket gateway —
  a large new infrastructure component, against "build the simplest thing that works; justify
  every layer or delete it", to reach a transport we already have.

  *Where the SIP trunk credentials are used:* they are real and they are kept. Vobiz is the
  SIP client, not Roma. The trunk (`28acf939.sip.vobiz.ai`, user `sarvam.weltec`) is what
  Vobiz authenticates against to place the outbound leg, what the console is configured
  against, and what identifies a call in Vobiz's logs. They live in `.env`
  (`VOBIZ_TRUNK_ID` / `VOBIZ_SIP_DOMAIN` / `VOBIZ_SIP_USERNAME` / `VOBIZ_SIP_PASSWORD`) and
  are redacted from logs. Nothing in the Python app consumes them, which is exactly why the
  app needs no SIP stack.

  *Audio unchanged:* μ-law 8 kHz both directions, which Vobiz supports natively. The cached
  `.ulaw` assets, the recorder's 8 kHz/stereo layout and the Silero rate are untouched.

  *What is structurally new:* Twilio took TwiML inline on `calls.create(twiml=…)` and never
  called back into this host. Vobiz POSTs an `answer_url`, so the process now serves HTTP
  (`/answer`, `/health`) as well as the socket, and must be publicly reachable on HTTPS.

  *Authentication:* the Twilio `accountSid` check was the only auth on `/ws` and has no Vobiz
  analogue. Replaced by a single-use token minted at `/answer` and redeemed at `/ws`
  (`telephony/streamauth.py`) — chosen over correlating on `callId` because that would depend
  on an unverified payload field whose failure mode is silent, total, and only visible on a
  live call. IP allowlisting is the right second layer once the host has a static IP.

  *Unverified, deliberately left off:* the REST hang-up endpoint. `<Stream>` omits
  `keepCallAlive`, so the call ends when the socket closes and the carrier does the teardown.
- **Cloudflare Tunnel: temporary integration-testing scaffold. NOT part of the production
  architecture (2026-07-31).** What Vobiz actually requires is that `PUBLIC_BASE_URL` name a
  publicly-reachable HTTPS/WSS host, since it must reach TWO endpoints (`POST /answer` and the
  websocket) and the app serves plaintext HTTP on 0.0.0.0:8020 with no TLS and no proxy-header
  handling. The tunnel supplies that address only for the current integration-testing phase,
  because the machine running these calls sits on a private network. **On deployment to the
  permanent public server** — real DNS, a certificate, a reverse proxy in front of uvicorn —
  `PUBLIC_BASE_URL` points straight at it, the Vobiz Application's Answer URL is set to the
  same host, and **the tunnel is removed**. Not migrated, not proxied through: removed. There
  is zero Cloudflare code or dependency in `src/`, so that removal is a runbook edit and not a
  code change. Do not build anything that assumes a tunnel. Deployment artifacts (service
  unit, proxy config, container, CI) are deliberately NOT built during the integration-testing
  phase — the requirement on this phase is only that nothing depends on the tunnel, which
  `PUBLIC_BASE_URL` already satisfies.
- **LOCKED: Roma is an INBOUND agent for this phase (2026-07-31).** Leads ring `+917971543192`;
  we do not dial them. Outbound is unavailable until Weltec supplies `VOBIZ_AUTH_ID` /
  `VOBIZ_AUTH_TOKEN`, and inbound `<Stream>` needs neither.

  *Behavioural model:* the **six inquiry-team source calls**. They are inbound — the counsellor
  picks up knowing nothing and asks from scratch — which is exactly Roma's situation now.
  **Roma's own three recorded calls are OUTBOUND and must NOT drive opening flow**: she greets
  by name, references a prior WhatsApp chat and asks permission to talk, none of which exists
  when the lead is the one who dialled. They remain valid for P3–P7 (course answers, objection
  handling, closing), which do not depend on who initiated.

  *What follows from it:* nothing is known at connect. `lead_name` starts empty and is the
  first discovery slot; P1 confirms this is Weltec and lets the caller state their business;
  its exit no longer requires an affirmation.

  *Outbound-only concerns that DO NOT APPLY this phase:* DNC registry, the 09:00–21:00 calling
  window, dialer pool, retry policy, voicemail detection. They stay in `dialer/precall.py` for
  when dialing returns — not deleted, just not on the path.

  *Who can reach Roma is no longer a list.* "Test handsets only" was an outbound rule: it
  constrained who we DIAL. Inbound, anyone holding `+917971543192` can ring it from any
  number, so the exposure is the DID itself.

  *What that does and does not cost.* **DNC and the calling window do not apply to inbound,
  and should not** — one answers "may we dial this person?" and the other "is this a decent
  hour to ring them?", and neither question exists when they dialled us. The **spend cap did
  have to be rebuilt** for inbound (`telephony/media.py`, at answer time), because
  `precall_check` runs in the dial path and the cap was otherwise measured at teardown and
  enforced nowhere. **The one genuine open item is the recording-consent line**, still the
  compliance placeholder: no disclosure is spoken and every recording is discarded. Fine for
  supervised test calls; it must be closed before real leads are routed here for any length
  of time.
- **LOCKED: outbound stays OFF this phase, credentials or not (2026-07-31).** Weltec supplied
  `VOBIZ_AUTH_ID` / `VOBIZ_AUTH_TOKEN`, which makes the Call API reachable. **That is
  capability, not clearance.** Dialing does not resume until BOTH hold: the dial-path Gate 0
  checks (`dialer/precall.py` — DNC registry, calling window, spend) are wired to real data
  rather than `StubRegistry`, and a signed-off recording-consent line exists. Reaching a lead
  who never asked to be called is the failure mode with legal weight, and the credentials
  removed the only thing that was accidentally preventing it.
- **Roma runs as ONE process, one worker.** Recorded because it is a property of the design,
  not of the deployment: the `/answer` → `/ws` token is an in-process dict
  (`telephony/streamauth.py`), so a second uvicorn worker rejects the calls whose tokens it
  did not mint — roughly (N-1)/N of them, intermittently, looking exactly like a carrier
  fault. Scaling past one process means moving that store to Redis (`SETEX` + atomic
  `GETDEL`), not adding `--workers`.
- **Rejected:** LiveKit (Pipecat covers orchestration), LangGraph (phases are state, not a
  per-turn graph). Reintroducing either requires a written justification here.
- **State store:** Redis (call-state, cache, queue).
- **Deploy: UNDECIDED — there is no Vadodara host.** Corrected 2026-08-01. This line read
  "Vadodara, self-hosted" from the earliest planning notes and was repeated across docs until
  it was being cited as an available fallback in live debugging. **No such machine exists.**
  Nothing has been provisioned, and "move it to Vadodara" is not an action anyone can take.
  What is actually required is *a publicly reachable HTTPS/WSS origin* — self-hosted, VPS or
  managed, in or near Mumbai for RTT to the Vobiz edge. Until one exists, the cloudflared
  quick tunnel is the ONLY path and its limits are the deployment's limits.
  **Action:** choose and provision an origin; then verify RTT (`scripts/rtt_mumbai.py`).
- **LOCKED: no Node backend for the web UI (2026-08-04).** The operator UI is React (Vite) on
  :3030 talking DIRECTLY to the existing FastAPI on :8020. An Express layer was considered and
  rejected: it would only proxy JSON to a service we already run, and every proxy hop is another
  place the dial gate can be bypassed or a lead token can be logged. See `docs/13-web-ui.md`.

  *Gate 0 is config-only, deliberately.* `POST /api/call` builds its DNC allowlist from
  `CONSENTED_NUMBERS` and never from the request body. This is the one place the web path
  diverges from `scripts/place_test_call.py`, which builds `StubRegistry(consented={args.callee})`
  — consenting to the very number being dialled, which makes the DNC check on that path
  decorative. A browser form doing that would be a publicly reachable bypass of docs/07's dial
  gate. `tests/telephony/test_api_call.py` pins it.

  *Still local only.* The endpoint has NO authentication and can ring real phones and spend the
  OpenAI budget. Run the backend on `--host 127.0.0.1` while the UI is up. **Production deploy
  remains UNDECIDED** (see the Deploy entry below) and this must not be exposed until it has auth
  — CORS constrains browsers, not `curl`.
- **Consent allowlist REMOVED from the web dial path (2026-08-04).** `POST /api/call` no longer
  requires the callee to be in `CONSENTED_NUMBERS`; any valid **Indian mobile** (`+91`, ten
  digits, opening 6-9) dials unless it is on the new `DND_NUMBERS` denylist.

  *Why.* The allowlist was a TESTING safeguard, not the production consent basis — that is
  Weltec's enquiry list. Editing `.env` and restarting per test number was real friction with
  no matching safety value while testing.

  *What it costs, stated plainly.* `StubRegistry` **was** the DNC check on this path, so
  removing the allowlist flips Gate 0 from **deny-by-default to allow-by-default** here. That
  contradicts `dnd.py`'s original posture ("unknown numbers are BLOCKED — the strict reading of
  the Gate-0 rule 'never dial unguarded'"), which is why the web endpoint uses a separate,
  explicitly-named `DenylistRegistry` rather than quietly redefining `StubRegistry` under a
  docstring promising the opposite. `scripts/place_test_call.py` still uses the allowlist class.
  **An empty `DND_NUMBERS` blocks nothing**, and with no TRAI/DLT feed wired (docs/07 §consent)
  we have no real suppression data — saying so beats pretending otherwise.

  *What replaces it as the boundary:* bearer auth that **fails closed** (no `API_TOKEN` ⇒ 503),
  Indian-mobile-only validation, the calling window, the spend cap, an hourly dial cap
  (`MAX_CALLS_PER_HOUR`, which catches the stuck-retry failure the spend cap only sees after the
  money is gone), and `BIND_HOST=127.0.0.1` as the DEFAULT rather than a runbook line.

  *Authentication is still absent in the sense that matters.* The browser sends the token from
  `VITE_API_TOKEN`, which Vite inlines into the JS bundle — it is not secret from anyone who can
  load the page. It guards the NETWORK boundary and nothing else, and CORS constrains browsers,
  not `curl`. **This must not be reachable off localhost until it has a session-based login.**
  The endpoint rings real phones and spends real budget. Production deploy remains UNDECIDED.
- **Language:** code-mix mode on Saaras + Bulbul (Gujarati/Hindi/English).
- **Post-lock:** async queue → CRM/calendar. Never a live write inside the turn.
- **D3 (money):** absolute zero-tolerance. Only "No-cost EMI available" permitted.
- **Endpointing:** 850ms default, adaptive; Silero VAD (model-based). Re-tune on live audio.
- **Recording (v1):** store every recording post-call. Simple. No transcription/analytics v1.
- **Models:** LLM = GPT-4o-mini. TTS = Bulbul **v2 for now**, move to **v3 once calls run
  properly** (A/B v3 vs v2 on real Gujarati first — v2 ₹4.50/call vs v3 ₹9). STT = Saaras.
- **Cost cap (testing):** OpenAI mini spend **< ₹100 for the entire testing phase** (revised
  down from ₹200 on 2026-07-26 — ₹100 is the actual remaining balance), enforced by
  a hard spend limit in the OpenAI dashboard. Sarvam = free credits first. Real limiter on test
  volume is Sarvam credits, not OpenAI.
- **Production cost target:** ₹3–4/call via v2 + fixed-phrase TTS caching + short turns. TTS is
  the dominant cost; LLM is near-zero. (Twilio ~₹2.5–5/call is a separate line, not in this.)
- **Observability:** native — `response.usage` token logging + OpenAI dashboard for LLM cost;
  structured logs / Pipecat metrics for pipeline latency. **LangSmith considered and rejected**
  (built for LangChain/LangGraph, which this stack doesn't use; hand-instrumenting raw OpenAI-SDK
  calls isn't worth it vs native usage logging). Re-adding requires showing what changed.
- **Optimization scope (`docs/02`):** APPLIES — prompt caching (static prefix), max_tokens per
  phase, structured call-state (not raw transcript), fixed-phrase TTS/KB cache, per-phase model
  routing, STT+slot confidence thresholds, noise-suppression/AEC (verify Twilio's first).
  EXCLUDED with reasons — SQL/schema-linking (no in-call DB), all LLM-server internals (KV
  cache, FlashAttention, PagedAttention, MQA, speculative/continuous/dynamic batching — OpenAI's
  side), speculative execution, semantic response caching, prompt/memory compression &
  context-window management (call too short), LLM-as-judge & hallucination metrics (offline eval
  only), ReAct/ToT/CoT (one turn, no mid-turn tools). Re-adding any EXCLUDED item requires
  showing what changed.
- **Secrets loading:** `pydantic-settings` (`BaseSettings` + `SecretStr`) over hand-rolled
  `os.environ`. Reasons: declarative fail-fast validation, `SecretStr` auto-redacts in
  repr/tracebacks, and pydantic is already in the OpenAI/Pipecat tree. (Gate 0, `docs/07`;
  detail in `docs/superpowers/specs/2026-07-20-secret-scaffold-design.md`.)
- **Packaging/tooling:** `uv` + `pyproject.toml`, `src/roma/` layout, Python 3.11+. Establishes
  the pattern the whole build inherits.
- **Secret source:** environment / `.env` only — no external secret store (Vault/cloud). Correct
  for a single self-hosted origin; revisit if the deploy model changes (the origin is still
  UNDECIDED — see Deploy above).
- **Pre-TTS filter (Gate 0, `docs/04`):** standalone `src/roma/guardrails/` package
  (normalize → lexicon → filter). Allow-case is **negation-gated pushback** (filter reads
  Roma's line only; a rebuttal cue within `NEGATION_WINDOW=3` tokens of a signal opens the
  gate). Clauses split on `, ; ।` and a spaced dash — never an unspaced hyphen (ranges/
  compounds stay intact). CERT is a real 5th category, `CERT_BLOCK_ENABLED=True` pending D1.
  Fail-safe hard-fails the turn to a canned line; absence of the filter = absence of a
  callable build. Spec/plan under `docs/superpowers/` (`…/specs/2026-07-20-pretts-filter-design.md`).
- **Prompts (runtime data, `docs/11`):** live in `src/roma/prompts/` as Markdown, not code,
  loaded at startup. Assembled per turn in the order **persona → hard_rules → phase →
  call-state**. That order is **load-bearing for prompt caching** (`docs/02`): persona +
  hard_rules + the phase fragment are byte-identical every turn in that phase and form the
  cacheable static prefix; call-state is the variable tail and MUST come last. Reordering for
  readability breaks the prefix → cache misses, higher input tokens, worse TTFT every turn.
  `hard_rules.md` and the filter lexicon (`docs/04`) must stay in agreement.
- **Pre-dial gate (Gate 0, `docs/07` §consent):** standalone `src/roma/dialer/` package
  (`consent → window → dnd → precall`), the mirror of the pre-TTS filter for the dialer path.
  The Step-1 dialer MUST call `precall_check` and dial only on `may_dial=True`, speaking
  `consent_line` first. **DND posture = allowlist-only:** a number dials only if on an explicit
  consented-lead allowlist (unknown → BLOCK), the strict reading of "never dial unguarded."
  `StubRegistry` is NOT the TRAI registry — real DND/DLT scrubbing needs Weltec's provider
  (**owner:** Weltec) and swaps in via the `DoNotCallRegistry` protocol. **Calling-window** =
  09:00–21:00 IST `[start, end)`, documented module constants (not env), confirm exact TRAI
  window with Weltec compliance. **Consent line** is a placeholder constant pending Weltec
  compliance sign-off — structural home is final, only the wording is a TODO. Fail-safe BLOCKs
  on any internal error (never dial unguarded). No Twilio API here — outbound-call wiring is Step 1.
- **Source-transcript provenance:** the Digital Marketing counselling-visit transcripts are the
  **VISIT, not Roma's call**. Roma books the visit; the transcripts are what happens *at* it.
  Usable for course content, register/phrasing, and **negative filter tests** (lines Roma must
  never say) — **never for call flow or money behaviour**. At a visit a counsellor quotes real
  fees correctly; on Roma's call that same number is catastrophic, so mining visit money-talk
  into a call prompt would reintroduce exactly the leak the filter (`docs/04`) exists to stop.
- **Five-minute call ceiling** (2026-07-26, user requirement): a Roma call is capped at 5:00.
  Enforced in code, not in wording — `roma.controller.pacing` converts elapsed seconds into
  four bands (`open` / `hurry` 3:00 / `close` 4:00 / `over` 5:00). Each band renders an
  imperative line into the **phase** fragment as `{{pacing}}` (never persona/hard_rules — the
  cached prefix must stay byte-identical, `docs/11`); past `hurry` the machine force-pivots
  P2/P3/P4 straight to P5; at `over` `telephony.closing.CallCloser` ends the call on the next
  `BotStoppedSpeakingFrame`, and unconditionally 30s later. P1, P6 and P7 are exempt from the
  forced pivot — skipping the inquiry confirm, abandoning a half-answered objection, or
  dragging the call out of the readback each cost more than the seconds they save.
  `P2_MAX_TURNS` drops 6 → `len(DISCOVERY_ORDER)` for the same reason: one turn per slot is
  all a cooperative lead needs, and an evasive one is better served by the booking.

- **Speculative classification on interim STT transcripts — REJECTED as infeasible**
  (2026-08-08, verification pass). The idea: classify the turn type (canned / filler+LLM /
  short-circuit) while the lead is still speaking, so the decision is ready when the final
  transcript lands (~100-200ms expected). The premise fails twice on this stack. (1) There
  are no interims: pipecat 1.6.0's `SarvamSTTService` hardcodes `is_final=True`, never
  constructs an `InterimTranscriptionFrame`, and the vendored SDK exposes no partial field
  at all — enabling this needs a change to Sarvam's server contract, not Roma code
  (`telephony/transcript.py` and `turntaking.py` both document the same fact). (2) Even
  with interims, the classification itself is a sub-millisecond lexicon check; the latency
  ahead of the decision is slot extraction (~0.9s) and Sarvam's own finalize (p99 1.17s),
  neither of which a speculative *classifier* touches. Speculative *generation* stays
  rejected separately (barge-in waste). The defensive interim branches stay so a future
  pipecat/Sarvam release lights them up rather than dropping frames. Revisit only if
  Sarvam ships streaming partials.
- **Fixed-phrase TTS cache — the docs/06 design, now implemented** (2026-08-08). Every
  fixed line Roma speaks (guardrail substitutions, safe lines, sign-off, the off-topic
  deflection bank) is pre-rendered offline and served from Redis keyed by a hash of the
  normalized text — change the wording, the key changes, the old entry is ignored. Served
  from INSIDE the TTS service (`run_tts` checks the cache first) so the full frame
  lifecycle — `TTSStarted/Stopped`, `BotStoppedSpeaking` — is preserved; a pipeline-level
  bypass with raw audio frames is exactly the no-`BotStoppedSpeakingFrame` bug class that
  produced the five-minute silent call (0ce455b0). Cache miss or Redis down degrades to
  live TTS, same posture as the opener store.

## Open — do not block the build
- **Branch visiting hours — CONFLICT, blocks nothing but is currently wrong in one direction.**
  The user stated **9 AM – 6 PM** on 2026-07-26 and `timeresolve.VISIT_HOUR_END` was set to 18
  on that basis (hard rule 8 and every prompt example moved to "shaam paanch baje"). The
  counselling transcripts say otherwise: *"Morning 8 o'clock institute start hota hai, 8:30 tak
  open rehta hai"* (Nirav Parmar), *"shaam ko 8:30 baje tak we are open"* (Heer Punjabi), and
  *"kaal dedh vagya sudhi aapnu open hashe savaar thi, half day che Sunday na"* (Tulna Joshi)
  — i.e. weekdays 08:00–20:30 and a Sunday half-day to 13:30. The user's figure is treated as
  authoritative (they are Weltec; the transcripts may be a different branch or period), so the
  code stays at 9–18 and simply refuses bookable-but-real slots. **Sunday is not modelled at
  all** — `resolve_time_slot` applies the same window to every weekday. **Owner:** Weltec.
- **D1 (certs):** which certs are real? Sources conflict (Weltec-only vs Google/Meta vs
  government/IBM). Until Weltec confirms, claim ONLY the Weltec certificate; filter softens the
  rest. **Owner:** Weltec. One email.
- **Saaras WER on Gujarati:** unmeasured, and the highest technical risk. The lead's audio is
  quiet on 8kHz — if my detector struggles to hear it, Saaras may too. **Test:** run one call
  clip through Saaras, diff against the human transcript, compute WER. **Owner:** us (needs a
  live Saaras call — Claude in chat cannot run it).
- **OpenAI model per turn:** quality vs TTFT. Measure real TTFT before committing.
- **Recording capture method:** Twilio-side vs local media capture (`docs/09`). Decide at
  build; local aligns with the on-prem/PII choice.
- **Real TRAI DND/DLT scrubbing:** the Gate-0 `StubRegistry` is allowlist-only, not the real
  registry. Wiring Weltec's registered DLT/DND provider replaces it via the `DoNotCallRegistry`
  protocol. **Owner:** Weltec. Also confirm the exact TRAI calling-window (assumed 09:00–21:00 IST).
- **Consent-line wording:** `CONSENT_LINE` in `src/roma/dialer/consent.py` is a placeholder;
  Weltec compliance must sign off the exact recording-disclosure text. **Owner:** Weltec.

## Karpathy rules
- The `andrej-karpathy-skills` plugin is chosen but its exact rules aren't transcribed. When
  loaded, paste the rules into `CLAUDE.md` and hold the build to them.