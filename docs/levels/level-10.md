# Level 10 — Telephony and model serving

**v4 sections:** 41–44. **Status:** Cloud Twilio path present; local-model serving/live integration planned.

## Entry gate

Levels 7/8/9 plus earlier local-model and booking gates.

## Scope when implementation is requested

Integrate conditional retrieval into the real-time budget/cancellation path and connect the validated local providers to the existing Twilio/Pipecat media lifecycle. Reconcile inbound/outbound opener and prompt behavior.

Introduce vLLM only after direct inference benchmarks, with warm/readiness checks and measured GPU admission/capacity. Serve local ASR/TTS where justified; validate queueing and cold-start behavior.

Keep local stereo capture/post-call conversion and add authorized audited time-limited recording access after consent approval. Demonstrate a separately authorized phone call with zero external GenAI API use.

## Existing reuse in Voice_Agent

Twilio bidirectional adapter/native serializer, Pipecat media, lead custom parameters, phrase cache and local recording/database handoff.

## Acceptance gate

- [ ] Authorized local-model telephony call meets measured timing/quality and transport format contracts.
- [ ] Retrieval/model timeouts and disconnect/barge-in clean up all work and carrier output.
- [ ] Readiness/admission prevents overloaded or cold providers from accepting unusable calls.
- [ ] Traffic evidence proves no external GenAI calls; recording access/consent rules hold.

## Boundaries and advanced work

No automatic Twilio migration or assumed direct SIP/RTP support. No live call or provider spending in a documentation task.

## Evidence

Record revision, reproducible commands, environment/data/configuration identity, actual outcomes, demo/reviewer result and limitations here when this level is implemented. Existing foundations do not automatically pass the full gate. Use the [verification contract](../12-verification.md), [baseline](../roadmaps/level-00-baseline.md) and [decision register](../decisions.md).

Return to the [documentation index](../README.md).
