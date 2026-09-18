# 05 — Endpointing, VAD & Barge-in

Thresholds derived from acoustic analysis of the 8 source-call recordings (403 pooled pauses).
Treat every number as a **starting value to re-tune on real Twilio audio** — the recordings
are WhatsApp-codec, not 8kHz μ-law.

## Three layers (separate jobs — do not collapse into one silence timeout)
```
Layer 1  VAD          → is there speech this 20ms frame?   (Silero, model-based)
Layer 2  Endpointing  → is the USER's turn finished?        (silence + semantic)
Layer 3  Barge-in     → did user talk over Roma?            (interrupt + flush)
```

## Layer 1 — VAD
**Model-based (Silero), never an energy/dB gate.** Forced by the recordings: the lead's audio
is quiet and sits over a continuous noise floor (~1% true silence in the noisiest file). An
energy gate misses the quiet lead or triggers on hiss. Frame = 20ms (Twilio native). Don't
pre-AGC before VAD; Twilio's per-leg audio is cleaner than the mono mixes.

**Noise suppression / AEC (acoustic echo cancellation).** The source audio showed a continuous
noise floor and a quiet far-end, so the front-end matters. Two concerns: (a) background noise on
the lead's side degrading STT, (b) Roma's own TTS echoing back into the input and false-
triggering barge-in. Twilio applies some noise suppression / echo control at the carrier level —
**verify what's already handled before adding our own**, so we don't double-process and distort
the quiet lead further. If added, it sits before VAD in the input path. Do not over-engineer
this until real Twilio audio shows a measured problem.

**Resolved (2026-08):** concern (b) is measured absent on this stack. Across 20 recorded
calls the lead-channel RMS while Roma speaks never exceeds 0.78x its RMS while she is
silent — the carrier's per-leg separation already suppresses her voice on the inbound leg,
so barge-in needs no AEC and `ENABLE_BARGE_IN` defaults on. Measure again before blaming
echo (see the no-acoustic-echo note in `build_user_params`).

## Layer 2 — Endpointing (the core number)
**Default turn-final silence: 850ms.** Pause distribution: p50 0.48s, p75 0.66s, p90 0.90s,
p95 1.15s. 52% of pauses are 0.3–0.5s; cutting at 500ms false-triggers on >half of natural
pauses.

**Adaptive (better than fixed):**
- ~500ms after a clear terminal answer ("haan", "Vadodara", "2025", "theek hai").
- 850ms default.
- ~1300ms after a continuation marker ("matlab…", "actually…", "ek minute…", "haan to…").
  Build the marker list from the transcripts.

**Backchannel guard (critical for code-mix):** `haan`, `ji`, `hmm`, `achha`, `haan haan`
spoken WHILE Roma talks are NOT turn-takes. If a user-speech span is (a) < ~600ms AND (b) in
the backchannel lexicon AND (c) Roma is mid-utterance → log it, keep talking. Else → barge-in.

## Layer 3 — Barge-in
On genuine user speech during Roma's turn:
1. Cancel in-flight LLM generation.
2. Flush Bulbul output buffer.
3. Send Twilio Media Stream `clear` to drop already-buffered audio (else Roma overtalks ~1s).
4. Cancellation routes THROUGH the pre-TTS filter, not around it.

## Hiding the 850ms — filler token
The instant endpointing fires, play a 200–300ms **cached** filler ("achha…", "haan ji…") from
an audio cache (not a live TTS call), then stream the real sentence behind it.

## Config summary (current values, re-tuned on live Twilio audio)
| Param | Current | Env var | Source |
|---|---|---|---|
| VAD | Silero, 20ms | — | noise-floor finding |
| VAD stop (hangover) | **450ms** (runs as 448ms — pipecat rounds to 32ms frames) | `VAD_STOP_SECS` | 2026-08-04 retune, call 932b6c88 (~288ms/turn recovered) |
| Endpoint default | 850ms | `ENDPOINT_DEFAULT_SECS` | pause p90 |
| Endpoint terminal | 500ms | `ENDPOINT_TERMINAL_SECS` | adaptive |
| Endpoint continuation | 1300ms | `ENDPOINT_CONTINUATION_SECS` | adaptive |
| Backchannel max | 600ms | `BACKCHANNEL_MAX_SECS` | transcripts |
| Filler | 200–300ms cached | `ENABLE_FILLER` | latency mask |

**`VAD_STOP_SECS` and the endpoint default were once the same 850ms knob; they are separate
now.** The VAD hangover was re-tuned to 450ms on live μ-law audio while the endpoint default
stayed at the pause-distribution 850ms. The adaptive endpoint values (500/850/1300) run on
**both** barge-in branches — they were once gated behind `ENABLE_BARGE_IN` and every tuned
value silently vanished with the flag, so the gate was removed (`build_user_params`). Only
the backchannel guard itself needs the flag.

**Tuning these (docs/10 Step 7).** All five are `Settings` fields, so re-tuning on the
production origin is an env change, not a code edit. The reference values live beside the
lexicons in `telephony/backchannel.py` (and `media.VAD_STOP_SECS`); tests pin them to the
`Settings` defaults so the two cannot drift.

Move **one** number per call and read the `endpoint timing:` line the teardown prints — it
carries `vad_stop_secs`, `barge_in`, and the measured VAD-stop → final-transcript latency,
so a run's timings are always attributable to the config that produced them.

Measure before moving anything. Turn-final delay is `vad_stop_secs` (tunable) **plus**
Sarvam's finalize latency (not). If the measured finalize time dominates, lowering 850ms
cannot improve perceived response and the effort belongs elsewhere. `vad_stop_secs` is also
not a free dial: it does triple duty as endpoint floor, Sarvam flush trigger, and the
`effective_stt_wait` subtraction in pipecat's stop strategy.