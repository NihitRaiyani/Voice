# Turn pipeline and timing

The actual processor list is in `roma/realtime/pipeline.py`; opening/noise/transcript/watchdog/close guards supplement the speech chain below.

1. Verify Twilio WebSocket signature and start Account SID; resolve custom lead metadata.
2. Decode carrier audio, run Silero/endpointing and obtain a final transcript.
3. Normalize input, apply opening/noise guards and deterministic conversation routing.
4. Extract bounded slot meaning where required, resolve dates in code and choose the stage/action.
5. Use approved direct wording where possible; otherwise generate wording from the selected provider.
6. Aggregate speech units, apply confirmation/pacing and deterministic pre-TTS safety.
7. Synthesize or play manifest-verified fixed speech; sanitize/send audio and capture local media.
8. Cancel/flush on interruption or disconnect; finalize state and queue/spool background work.

Use one main wording generation per turn. Structured extraction is separately measured; direct routing may require no generation. A phrase cache hit skips synthesis, not automatically ASR or generation.

## Safety and cancellation

Never send raw partial model tokens directly to TTS. Sentence/chunk aggregation and final safety remain inside the interruption lifecycle. On barge-in, stop generation, discard partial aggregation, cancel synthesis and clear Twilio output before later stale audio can play. Safe substitution handles blocked text and validation failure.

Level 7 proves bounded queues, pressure, timeouts and late-result cleanup for the local stack. Level 9 retrieval runs only when eligible; Level 10 adds retrieval timing/cancellation to live media. No retry can outlive its useful turn budget or bypass safety.

## Measurements

Measure user-stop-to-first-audible-response directly. Also record endpoint decision, ASR finalization, extraction/routing, retrieval, first token, first safe sentence, TTS first audio, playback and database commit. Report P50/P95 at L0 and P50/P95/P99 later, with sample count/language/noise/hardware/configuration and cold/warm status.

Do not add stage percentiles to create an end-to-end percentile. Fillers can improve perceived responsiveness but are not evidence of an actual completed answer. Roadmap targets such as 1.2 seconds are hypotheses until L12 evidence supports an SLO.

Current source uses GPT-4o, Saaras v3 code-mix and Bulbul v3/ishita/Hindi at pace 1.05. `openai_budget_inr` defaults to ₹200; this is a spend ceiling, not remaining account credit. Local models and their queues have different resource/cost limits and require new benchmarks.

See [audio](05-endpointing-vad.md), [prompts](11-prompts.md) and [verification](12-verification.md).
