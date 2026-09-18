# 09 — Recording Storage

Scope is deliberately simple (per the v1 decision): **when a call ends, store the recording.
That's it.** No transcription pipeline, no analytics, no dashboards in v1.

## Flow
```
call ends → pipeline pushes {call_sid, recording_ref} to queue:postcall
          → post-call worker fetches the recording → stores it → acks the job
```

## Two ways to get the recording (pick one at build time)
1. **Twilio call recording** — enable recording on the call; Twilio stores it and gives a
   recording URL/SID. The worker downloads it to your store. Simplest; recording lives on
   Twilio first (mind retention + PII there).
2. **Local capture from the media stream** — Pipecat/Twilio media frames are written to a file
   as the call runs; the worker finalizes it on call end. Keeps audio on your infra (fits the
   Vadodara self-host choice), no third-party copy.

Given the on-prem Vadodara decision and PII locality, option 2 aligns better — but option 1 is
faster to ship. Decide explicitly; don't leave it implicit.

## Storage rules
- **Naming:** `{date}/{call_sid}.{ext}` — sortable, unique, ties back to call-state.
- **Location:** access-controlled store (see `docs/07`). Not a public bucket. Not world-readable.
- **Durability before ack:** the queue job is acked ONLY after the recording is durably
  written. A killed worker must never silently drop a recording.
- **Metadata (minimal):** alongside each recording, store `{call_sid, timestamp, duration,
  outcome, locked_slot}` — enough to find it later. Nothing more in v1.
- **Retention:** define a window (compliance + storage cost). PII-bearing; don't keep forever
  by default.

## Explicitly out of v1
- Auto-transcription, WER scoring, scorecard generation, sentiment, search. These are v2 and
  build on the stored recordings — the point of storing simply now is to not block on them.
