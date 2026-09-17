# HANDOFF → next session

## Telephony capability status (Vobiz, 2026-07-31)

| Capability | State |
|---|---|
| Inbound `<Stream>`: `/answer`, `/ws`, the whole voice pipeline | **Never yet exercised inbound — BLOCKED at Vobiz.** `07971543192` is linked to no application: every inbound attempt (18:56, 19:02, 21:21 on 2026-07-31) returns `failure_code 400 / failure_reason NUMBER_NOT_LINKED` and the caller's carrier plays "number invalid". Vobiz never fetches the answer URL, so nothing reaches this process. **Console fix — the `/Number/` API is 401 for this token.** |
| The pipeline itself (answer XML → `/ws` → STT → LLM → TTS → `playAudio`) | **Working, proven on OUTBOUND calls.** `b5273f93`, `6cb580df`, `1ee27ef1` are all `call_direction: outbound` — Roma dialling `919327858018`, not a lead ringing in. Everything learned about audio, turn-taking and barge-in came from those, and holds; only the *direction* was mislabelled. |
| Outbound dialing (`scripts/place_test_call.py` → REST Call API) | **Technically possible, deliberately OFF.** Credentials arrived 2026-07-31; see the LOCKED entry in docs/decisions.md. Do not dial until Gate 0 runs on real data and the consent line is signed off. Having the credentials is not clearance. |
| Application management via API | **Working.** `Roma-Weltec-Inbound` (app_id `27395576465236975`) was created this way. |
| Number ↔ application assignment via API | **Unavailable** — `/Number/` returns 401 for this token while `/Application/` returns 200. Attach numbers in the console. |
| REST hang-up | Unavailable and unneeded: `<Stream>` omits `keepCallAlive`, so the call ends with the socket. |

Vobiz authenticates its own SIP trunk and connects **to us**, so nothing on the inbound path
presents a credential. Both variables blank is a supported configuration: the media server
boots, answers, streams, and passes the whole offline gate. Only the REST features report
themselves unavailable and stop — `place_test_call.py` prints one sentence and exits 2.

**Numbers.** `+917971543192` is the Vobiz-side AI voice agent number — the number to RING.
Call it from **any handset**; inbound has no allowlist, because "verified caller ID" was a
constraint on who we DIAL and there is no dialing. `+919327858018` is just the phone usually
to hand.

## Pre-call checks — run these BEFORE ringing anything

The budget is ~1.5 calls. Each check below removes failure surface for free, in order.

**RESOLVED (2026-07-31): the "AI agent" on that number is Bolna.ai, not a Vobiz product.**
All 12 pre-existing Applications on this account point their answer URL at
`callback.bolna.ai`, so Vobiz is only carrying telephony for it. Re-pointing the number is
therefore an **ordinary Application reassignment** — nothing needs Vobiz to detach anything.

Target Application already exists: **`Roma-Weltec-Inbound`, app_id `27395576465236975`**
(created via the REST API, `answer_url` = the tunnel's `/answer`, POST, `hangup_url` cleared).

**Attaching the number is a console step** — `/Number/` returns 401 for this token. Before
attaching, **note which Bolna Application currently serves `+917971543192`** so it can be put
back. Do it at a moment you can ring immediately, so the first inbound call is your test and
not a real lead.

**1. The answer route, over the real tunnel. Free.** Rules out DNS, the tunnel, TLS, routing
and the XML in one request:
```bash
curl -X POST https://<tunnel>/answer -d 'CallUUID=precheck'
```
Wanted: HTTP 200, `<Stream bidirectional="true">`, `contentType="audio/x-mulaw;rate=8000"`,
and a `wss://<same-tunnel-host>/ws?t=…` inside it. Last run (2026-07-31, verified): 200, all
four correct.

The `localhost` version of this mistake is now caught at source: `scripts/serve_media.py`
**refuses to boot** when `PUBLIC_BASE_URL` names localhost, because every `wss://` URL it
minted would point Vobiz at its own loopback and the call would connect to nothing while this
process logged a clean 200.

**1b. Optional, also free** — connect to the `wss://` URL the XML just gave you and send a
`start`. Proves the socket survives the tunnel, not just the HTTP route. The token is
single-use, so fetch a fresh `/answer` for the real call.

**2. During the call, read the LOG first — it is binary.** The server must print:
```
media stream started: stream_sid=… call_sid=…
```
No line means the socket never reached this process, whatever came out of the earpiece.

⚠️ **That line only appears if the server was started as `scripts.serve_media:create_app`.**
The bare `roma.telephony.media:build_media_app` factory never calls `configure_logging()`, so
every `roma.telephony` INFO line is dropped and you get silence in the log while the call runs
normally and spends Sarvam credit. Verified the hard way on 2026-07-31 — the socket connected,
the pipeline built, Sarvam STT and TTS connected, and the log showed nothing.

`build_media_app` now **warns at construction** when logging is unconfigured (a WARNING
survives the last-resort handler, which is why it can be reported at all). If you see that
warning, the log is lying to you about everything below WARNING — restart via the entrypoint.

**3. Treat the audio as confirmation only, never as the primary signal.** Roma's first words
are exactly **"Hello, Sir"** (`canned.OPENING_LINE`, 1.51 s, off disk). Anything
else is not her — but an unrelated AI voice can sound entirely plausible, which is why the log
is checked first.

**4. After any ring, check Vobiz's console for webhook delivery attempts** against our Answer
URL. A logged delivery failure names the problem — DNS, TLS, timeout, 4xx — without needing
the audio at all, and it is the only view of what Vobiz *tried* to do when nothing reached us.

**The exposure is now the DID, not a callee list.** Anyone who has `+917971543192` can reach
Roma from anywhere, so do not publish it — no website, no ad, no WhatsApp broadcast — until
the consent wording is signed off. A real lead reaching a placeholder-consent agent that
skips the DNC check is the thing to avoid.

**What gates an inbound call, and what does not.** An earlier note here said inbound "skips
Gate 0" as though five protections had been lost. That was wrong in the parts that matter:

| Check | Applies inbound? |
|---|---|
| DNC / do-not-call registry | **No, and correctly so.** It answers "may we dial this person?" Nobody is being dialled — they chose to call us. |
| Calling window (09:00–21:00) | **No, and correctly so.** It stops us ringing someone at 6am. A caller who dials at 6am has decided the hour is fine. |
| Spend cap | **Yes.** Re-implemented at answer time in `telephony/media.py`; an exhausted budget closes the socket with 1013 before the pipeline exists. |
| **Recording consent** | **THE OPEN ITEM — see below.** |

⚠️ **Recording consent is the one real compliance gap for inbound, and it is still open.**
`CONSENT_LINE` is the `[PENDING WELTEC COMPLIANCE]` placeholder, so `consent_signed_off()` is
False, no disclosure is spoken, and the post-call worker DISCARDS every recording by design
(docs/09). Nothing unlawful is stored — but nothing is retained either, and any caller reaching
Roma hears no recording notice.

**That is acceptable for supervised test calls and not acceptable for real leads.** Close it
before this number carries live traffic for any length of time: get the wording signed off,
render the asset, and confirm `consent_signed_off()` flips True.

**Roma is now written as an inbound agent** (docs/decisions.md, LOCKED). She no longer greets
by name or references a prior WhatsApp chat: the caller rang us, nothing is known about them,
and the name is the first thing P2 asks for. P3–P7 are unchanged.

**The opener is cached audio, not a generated turn.** `canned.OPENING_LINE` ("Hello, Weltec
Institute", 1.51 s) plays off disk the instant the socket connects — the caller hears it at
t≈0 instead of waiting 3.28 s for the cold first LLM turn (CAfe5a00b) plus synthesis. Re-render
with `uv run python scripts/make_opening_clip.py --force` if the wording changes; it must stay
in Roma's voice (bulbul:v3 / ishita / 1.05) or the first line announces itself as a splice.

Consequences worth knowing before reading a transcript:
- `PickupGreeter` **stands down** when the asset is present. It exists to make Roma speak
  first on an OUTBOUND call; inbound the canned line does that, and letting it also fire
  would have her greet and then talk to herself two seconds later.
- **P1 is now a branch, not the opening script.** It runs only when the caller asks who
  picked up ("kaun bol raha hai", "Weltec hai?") — then it says who we are once and stops.
  A caller who states their business goes straight to P2.
- If the asset is missing, `media.py` logs an ERROR and falls back to the generated opening
  turn. The call still works; it just opens with the dead air this removed.

## Turn-taking, as of 2026-07-31 (call b5273f93)

**The perceived "Roma goes silent" was self-inflicted interruption, not dropped audio.**
Three of four interruptions on that call fired **1 ms after an STT final**, never from VAD.
The mechanism: Sarvam's finals land ~1.0 s after end-of-speech, and the endpoint was 0.55 s.
So Roma started answering *before* the transcript of the turn she was answering arrived —
and that late final then opened a NEW turn and cut her off 183 ms into her own sentence.

Restored to docs/05, set explicitly in `.env` so they are visible, not silent defaults:

| Param | Was | Now |
|---|---|---|
| `ENDPOINT_DEFAULT_SECS` | 0.55 | **0.85** (pause p90) |
| `ENDPOINT_TERMINAL_SECS` | 0.30 | **0.50** |
| `ENDPOINT_CONTINUATION_SECS` | 1.10 | **1.30** |
| `BACKCHANNEL_MAX_SECS` | 0.80 | **0.60** |

⚠️ **These were lowered deliberately** on 2026-07-27 and again on 2026-07-30, against
"lagging voice" reports. Raising them **will bring that lag back**. It is covered by the
cached filler token (docs/05 "Hiding the 850ms"), which is the lever docs/05 specifies —
**not** by shortening the threshold a third time. If Roma feels slow, lengthen the filler.

`vad_stop_secs` stays 0.75 and now sits BELOW the endpoint default, so pipecat logs
"VAD stop_secs differs from the recommended" every call. Expected, not a regression — that
knob does triple duty and moves one step at a time against measurements.

**Endpointing is no longer coupled to `ENABLE_BARGE_IN`.** The old early return in
`build_user_params` meant turning barge-in off silently reverted every tuned value to a flat
`VAD_STOP_SECS`, with nothing in the log saying so — the fourth instance of behaviour
vanishing with a flag nobody thought was load-bearing. Endpointing (docs/05 Layer 2) is now
built in both branches; the flag governs only interruptions (Layer 3). A
`turn-taking: barge_in=… endpoint_default=…` line is logged at call setup so the effective
numbers are never a guess again.

## Web UI — place a call from a browser (added 2026-08-04)

Two endpoints on the existing FastAPI, plus a Vite/React page. Full spec: `docs/13-web-ui.md`.

```bash
# backend — NOTE the host, this endpoint has no auth
uv run uvicorn scripts.serve_media:create_app --factory --host 127.0.0.1 --port 8020   # 127.0.0.1 is the default now
# frontend
cd web && npm install && npm run dev      # :3030
```

- `POST /api/call {"to_number": "+91…"}` → `{request_uuid, status}`; `409` with a
  `block:window` / `block:dnd` / `block:budget` reason when a gate refuses.
- `GET /api/call/{request_uuid}` → `{status, reason}` where status is
  `dialing | connected | ended | no_answer`.

**Set `API_TOKEN` in `.env` and the same value as `VITE_API_TOKEN` in `web/.env`, or every
request is a 401 (and a 503 if the server's is unset — it fails closed).**

No per-number config edit any more: the consent allowlist was removed 2026-08-04
(`decisions.md`). Any valid **Indian mobile** dials unless it is on `DND_NUMBERS`. That means
Gate 0 is allow-by-default here — the bounds are now auth, the calling window, the spend cap,
`MAX_CALLS_PER_HOUR` (default 20 → `429`), and `BIND_HOST` defaulting to `127.0.0.1`.

`no_answer` is derived from the record's age (60s), because Vobiz sends no status callback —
an unanswered call produces no event anywhere in this system. Do not go looking for the
writer; there isn't one.

**Do not expose port 8020.** The bearer token is inlined into the browser bundle by Vite, so
it is not real authentication — it stops another machine, not a person at the page. CORS
constrains browsers, not `curl`. A session login is required before this leaves localhost.

## Where we are
**A full verification + optimization pass landed 2026-08-08 and IS COMMITTED** (10 commits
on `fix/live-call-defects-and-5min-budget`). **1377 tests green**, ruff clean, evals 10/10.

Verified, with the verdict — these were audits, and three of the five found the system
already correct:

- **Redis (12 touchpoints):** everything on the live-audio path degrades. Two writes were
  unguarded and are now fixed — `put_dialing` 500'd *after* the phone was ringing, and a
  dead lead store surfaced as `502 carrier refused`, sending the operator to Vobiz for a
  Redis outage (now a typed `DialPrereqError` → 503 naming the store). The postcall worker
  loop no longer dies on a blip. New `tests/telephony/test_redis_midcall.py` kills Redis
  MID-flow; every prior failure test broke it before the flow started.
- **Endpointing:** the four values running today (850/500/1300/600ms) match docs/05, and
  the adaptive endpointer runs on BOTH barge-in branches — the older "only with barge-in"
  finding is stale. The drift was in `.env.example`, which still shipped `VAD_STOP_SECS=0.85`
  and would silently un-tune the 0.45 retune on any fresh deploy.
- **Filter SALARY hole:** already closed — "Tankhwah 30000 milegi" blocks as SALARY
  (verified by execution). One spelling was still open: `તનખ્વાહ` passed CLEAN, and STT runs
  `gu-IN`, so that is the script the noun actually arrives in. Fixed, with rows for all
  three scripts.
- **Phase machine:** objections and short-circuits already route from any phase. But a P2
  "batch kab hoti hai?" got NO real answer — the structure facts lived only in the p3/p4
  fragments the model was not holding. P2 and P6 now carry a compact approved fact list and
  answer inline without moving the phase.
- **Voice/gender:** every shipped Roma string was already correct. The defects were in the
  EVAL fixtures — 23 reference lines used the banned singular toward the caller — and in
  the lint, which covered 7 forms in `phases/*.md` only. It now sweeps every fragment, every
  fixed runtime string, and the eval scripts, in both directions.

Built:

- **Off-topic deflection bank** (`controller/offtopic.py`) — the fourth short-circuitable
  family. Lexicon-classified, per-category rotation so no repeat within a call, converging
  at the third drift to one warm line that re-asks the pending discovery question.
- **Intent-matched fillers** — `Dekhiye…` for questions, `Samajh rahi hoon…` for
  objections, chosen with lexicons that already existed. `fillers_played` now counts
  confirmed plays, not attempts.
- **Fixed-phrase TTS cache** (`telephony/phrasecache.py`) — docs/06's content-hash design,
  28 clips, served from INSIDE `run_tts` so the frame lifecycle is intact.
- **Speculative classification: REJECTED**, recorded in decisions.md — Sarvam emits no
  interim transcripts at all on pipecat 1.6.0.

## The ONE next task
**Push, and then get a clean measured call.** The pass is committed but the branch is not
pushed, and the live confirmation is still outstanding — see the note below.

## Landmines
- **Run tests with `uv run pytest`** — bare `python` is a different (conda) env. Async tests
  use `asyncio.run(...)`; there is no `pytest-asyncio`.
- **STT returns Gujarati script** (`gu-IN`, D3). Every lead-facing matcher MUST carry
  Gujarati spellings, and **must not use `\w`/`\W`** — Indic combining marks are not
  `alnum`, which is the bug above. One tokenizer only: `guardrails.normalize.tokens()`.
- **`var/` holds lead PII** (call audio + post-call spool) and is gitignored. That entry
  must stay in step with `Settings.roma_data_dir`.
- **`PostcallJob.raw` must never be re-serialised on ack** — `LREM` matches by exact string;
  a differing key order leaks the inflight entry forever and redelivers on every restart.
- ~~**Do NOT flip `ENABLE_BARGE_IN`** on the laptop→cloudflared path — no AEC.~~
  **RESOLVED 2026-07-31: stale, and it was already disproven twice before this.** Keep
  `ENABLE_BARGE_IN=true`. The caution assumed Roma's voice would loop back through the
  handset and self-interrupt. Measured across all 20 recordings: the lead channel's RMS
  while Roma speaks never exceeds **0.78x** its RMS while she is silent — there is no echo
  to cancel. Acting on the theory once already cost a live call (CA3e7f4c58: a 56-second
  monologue over a lead trying to interrupt twelve times). Vobiz also delivers **separate
  legs per speaker**, so our leg carries even less of Roma than Twilio's mono mix did.
  Real remaining constraint: docs/05 Layer 1 says verify what noise suppression Vobiz
  already applies before adding our own — that is a "don't double-process" note, not a
  reason to disable barge-in.
  The genuine barge-in defect was never AEC. It was the endpoint threshold — see below.
- **Recording storage is blocked on Weltec consent sign-off.** While `CONSENT_LINE` is the
  `[PENDING WELTEC COMPLIANCE]` placeholder the worker DISCARDS every recording, by design.
  Replacing the wording also requires re-rendering `assets/consent.ulaw`, or `canned._load`
  refuses it — now loudly at app BUILD, not as a silent dead call.
- **Live calls need a cloudflared tunnel**; test numbers only, 09:00–21:00 IST, ₹100 OpenAI
  cap (down from ₹200 — that is the real remaining balance). Sarvam STT connect is flaky
  (2 of 4 calls failed) — redial.
- **TTS is `bulbul:v3`, voice `ishita`, `pace=1.05`.** Never v2 — it renders code-mix badly.
- **SARVAM CREDITS RAN OUT mid-call on 2026-08-08 10:15:23** (call 3b639cf8). Sarvam sent
  `Insufficient credits` and closed the STT socket: `stt_alive=False`,
  `service_errors=1698`, and Roma could not hear the lead for the rest of the call. The
  guard did its job — the call degraded instead of crashing — but a deaf Roma is not a
  usable call. **Top the Sarvam account up before the next live test.** Note what spends
  it: `scripts/warm_phrase_cache.py --force` re-renders 28 full sentences (~150s of
  synthesis) in one go, which is almost certainly what drained the balance minutes before
  that call. Render without `--force` unless a wording actually changed.
- **There is no pre-call Sarvam balance gate.** `precall_check` guards the OpenAI budget
  only; nothing checks Sarvam before dialing, so this failure can only be discovered live.
- **Redis IS running locally now** (started by `scripts/start_roma.sh --dir var/`). Every
  live-audio path still degrades if it goes away, and `test_redis_midcall.py` pins that.
- **`dump.rdb` held lead PII and was TRACKED in git** until 2026-08-08. Untracked and
  ignored, and redis now dumps into `var/`. **The blob is still in history** — a
  `git filter-repo` scrub was deliberately deferred (destructive; this repo has not left the
  developer's machines). Do that before any push to a shared remote.
- **Editing a fixed line invalidates its cached audio.** The phrase cache is keyed by a hash
  of the text; a wording change makes the clip stale, which is REFUSED at load (it degrades
  to live TTS, it never speaks the old words) until
  `uv run python scripts/warm_phrase_cache.py` re-renders it. The same is true of
  `assets/consent.ulaw` and the fillers.
- **The postcall queue lists are unbounded** — `queue:postcall:dead` grows forever and
  nothing sweeps it. Known, deferred.
- **The API test suite judged the TRAI calling window on the real wall clock** until
  2026-08-08, so it silently only passed between 09:00 and 21:00 IST. Any new test that
  reaches the dial path must pin `roma.dialer.precall.in_calling_window`.

## Skill to pull next
**`roma-guardrail`** — before touching anything that decides what Roma speaks. It is the
docs/04 contract, and the session that just ended proved the filter can fail open silently.

After committing, the next build step is **docs/10 Step 7 — eval & tuning**: a replay
harness over the transcripts (phase hits, word caps, slot extracted, **filter never
leaks**), then tune 850ms / VAD sensitivity on real Vobiz calls. Pull **context7** for
current Pipecat APIs before coding against them.
