# Level 10 — Telephony and model serving

**Roadmap sections:** 41–44. **Status:** Owner-authorized local telephony, live retrieval, recording diagnostics and bounded speech-serving implementation present; combined offline verification passed. Native/live acceptance remains open.

## Entry gate and current scope

Earlier Levels 7/8/9 and local speech/booking gates remain independent. The owner explicitly authorized this implementation before those full gates passed; this does not waive their evidence requirements. Twilio/Pipecat remains the carrier/media baseline. `VOICE_SPEECH_MODE=legacy_cloud` remains the default; `local_http` explicitly selects loopback HTTP speech. Pinned Whisper Turbo MLX 4-bit, Indic-TTS, Claude Haiku 5.5 and Hindi-base Hinglish remain the selected identities. The roadmap's IndicConformer example does not change the selected ASR.

## Sections 41–44 implementation

- **41 live knowledge:** call-owned retrieval shares a turn epoch and bounded answer deadline and reuses approved cached waiting audio. The complete bounded Claude JSON quote must pass schema/span/output-policy, revalidation and audit checks before `StageControllerProcessor` uses `PunctuationChunker` to deliver sentences through final safety/TTS. With `local_http` and RAG enabled, app readiness synthesizes the approved Hindi `NO_EVIDENCE_LINE` through warmed local HTTP TTS into `PhraseCache`; a timed-out answer uses this fixed PCM without new inference. Interruption/disconnect invalidates stale work. RAG defaults off.
- **42 local telephony:** the opt-in path resamples validated mono carrier PCM for local ASR, returns local TTS as ordered 8 kHz/20 ms frames and preserves the caller leg for optional stereo capture. Inbound/outbound opener/controller policy remains code-owned. A call-owned `PlaybackWindow` reserves PCM in the local TTS producer before Pipecat enqueue; fixed raw cached assets are chunked/reserved before transport output. It permits at most 0.4 seconds of unacknowledged PCM and a 0.25-second enqueue wait. `WindowedOutputTransport` acknowledges completed software writes/pacing with a pre-write generation snapshot, ignoring stale acknowledgments. The controller advances the shared turn epoch and resets the window before awaiting interruption/cancel/end handling, including calls without RAG. Pressure latches failure and requests close/cancel. Per-message WebSocket byte limits and the one-second send timeout remain separate bounds. These are engineering bounds, with physical stop latency and native capacity still unaccepted. The legacy cloud profile is unchanged; ordered TCP/WebSocket handling does not establish RTP packet-loss recovery.
- **43 serving:** a separate single-worker loopback service has a distinct private bearer key, authenticated health/readiness, cache-only model warmup, bounded body/audio/text/deadline/inflight admission and sanitized errors. A timed-out native operation retains admission until it exits; cancellation cannot honestly promise native work termination. The at-most-two HTTP STT batch serializes independent requests and preserves the existing provider's internal two-chunk strategy. It is not cross-request GPU batching. Claude organization rate fields are operator inventory, not a distributed limiter.
- **44 recording:** existing consent-gated local dual-leg capture now awaits serialized off-loop writes and close/fsync, retaining bounded numeric write/close metadata in the durable post-call handoff. Audited JWT/RBAC, user-bound expiring playback and managed disposal remain the recording access contract. Recording remains disabled while disclosure/retention approval is pending.

## Evidence and limits

Combined checks passed; exact counts, fingerprints and the synthetic native probe are recorded in [verification](../12-verification.md#sections-4146-telephony-serving-telemetry-and-costs--2026-10-26). [Runbook](../runbook.md#local-speech-serving-and-telephony-sections-4144) owns commands. Fake ASGI/model/carrier evidence can establish bounds and cleanup, not real speech quality, carrier behavior or capacity. The capacity harness labels fake/local mode, reports attempted-request P50/P95/P99, throughput/errors and available process resources; unknown VRAM stays null.

## Acceptance gate

- [ ] A separately authorized local-model phone call meets measured timing, quality and transport contracts.
- [ ] Native retrieval/model timeout, barge-in and disconnect evidence proves useful cleanup/output invalidation.
- [ ] Cold/overloaded providers refuse calls; native combined-stack memory, admission and sustainable concurrency are measured.
- [ ] Traffic evidence proves Claude is the only external GenAI provider in the enabled local profile.
- [ ] Recording disclosure/retention and per-call consent are approved; audited access/disposal is operationally accepted.

No live call, microphone, paid request, provider/model switch, model download, working database upgrade or privacy sign-off follows from implementation or documentation. No Level 10 gate is passed. Full container packaging remains Level 13.

Return to the [documentation index](../README.md).
