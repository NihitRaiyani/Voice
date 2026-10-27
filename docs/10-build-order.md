# Claude roadmap progression and gate policy

Develop Levels 0–14 from the selected Claude source. Introductory prose says 0–13, but its table/body include optional Level 14. Engineering levels are distinct from Roma's seven runtime conversation stages.

| Levels | Outcome |
|---|---|
| 0–1 | Baseline/prerequisites, durable backend, contracts/mocks, versioned API and CI foundation |
| 2–3 | Claude text inference, deterministic safety/state and truthful transactional booking |
| 4–6 | Measured local ASR/TTS and end-to-end microphone prototype |
| 7–8 | Bounded streaming/cancellation/recovery and secure idempotent jobs/access/privacy |
| 9–10 | Conditional governed retrieval, local serving/GPU admission and Twilio integration |
| 11–12 | Real telemetry/analytics, regression/chaos and measured quality/capacity/SLOs |
| 13 | Full Docker deployment, clean-host setup and tested recovery/rollback |
| 14 | Optional evidence-backed optimization |

The [level briefs](README.md) own detailed dependencies/scopes/gates. Isolated ASR/TTS labs and booking may overlap after entry prerequisites pass; all required gates still precede Level 6 and production traffic.

## How to work

1. Verify repository root and read the requested level plus affected contracts/owner decisions.
2. Review entry evidence. Existing schema/adapter/tests are reusable work, not an automatic pass.
3. Plan one bounded implementation in that level's context; preserve the comparison baseline.
4. Implement only the requested level and run meaningful mocked/offline checks plus explicitly separated lab/live evidence.
5. Record results/limitations and demonstrate the gate before advancing.
6. After completing the requested level or section and its checks/docs, commit its scoped changes and push to the configured GitHub branch automatically. Report the commit link; preserve unrelated edits and exclude secrets/runtime data. This standing user authorization does not start the next level or authorize force-push.

One level brief owns status/evidence; do not create competing active plans or recurring journals. Earlier designs live in [the archive](archive/README.md). Historical compatibility constraints are assessed against actual code rather than blindly replayed.

Provider interfaces precede integration; direct local ASR/TTS inference precedes speech model serving, RAG begins at Level 9 and full Docker packaging begins at Level 13. Retain existing PostgreSQL/Redis Compose as pre-v4 compatibility tooling without making it the native foundation requirement.

Historical through-section-20 scope: align implementation and documentation through section 20 with the [Claude source](roadmaps/README.md). Claude Haiku 5.5 replaces OpenAI for wording/extraction. Earlier synthetic paid verification remains historical. The owner resumed a bounded paid Claude/microphone follow-up on 2026-10-22; see Level 6 for actual outcomes and remaining limits. Tests and the synthetic prototype remain mocked. This does not waive unfinished teacher/native gates or expand into section 21. No OpenAI requests or automatic provider fallback.

Current local development ASR is pinned Turbo MLX 4-bit with structural batching and reference-compatible waveform padding. [Level 4](levels/level-04.md#waveform-padding-repair-and-model-retirement--2026-10-22) owns the 15-case rerun and authorized full-v3 cache retirement. [Level 6](levels/level-06.md#supervised-claude-and-booking-follow-up--2026-10-22) owns real four-language-labelled input checks, owner clarity/relevance approval, the consent repair and confirmed TEST ONLY microphone booking/SQL restore. No downloads, model cascade or production-provider change occurred in that follow-up. Formal native/representative and voice latency gates remain pending; no section 21 work is implied.


Historical section 21 increment: structured logging, with offline tests and synchronized docs. Reuse the existing redacting stdlib logger and safe error enum; add task-local request/call/turn context and a reviewed event vocabulary without model/provider, paid/microphone, working-database or conversation-policy changes. Prior native/representative and latency gates remain pending. That increment did not start section 22.


Historical completed increment: sections 22–23 opt-in bounded queues, audited sentence streaming, CPU isolation and shared barge-in/disconnect cancellation. The user explicitly requested these capabilities; unfinished native/representative and latency gates are not waived. Default microphone half duplex and the production carrier/providers remain unchanged. Advanced epochs wait for their stated core gate; sections 24–25 are not started. Commit and push the verified scoped increment under the owner's renewed standing instruction.

Historical section 24 increment: Redis hot state and supervised SQL milestone recovery. Reuse existing caches/holds/counters; add leased call ownership, normal expiry reconstruction, explicit checkpoint failure/retry/lag, and SQL-authoritative booking degradation. Wire persistence into production composition and the existing local prototype without changing speech providers or transport policy. Advanced Streams/replay and section 25 remain pending; no gate is passed by source/offline evidence.

The owner explicitly authorizes sections 25–28 in order, using parallel independent implementation/review work with scoped commits. Sections 25–28 now add provider reliability, supervised jobs, SQL-authoritative webhook leases and JWT/RBAC over the preserved foundations. Operational auth setup remains explicit; code/tests do not upgrade working roma. Paid/microphone/native acceptance and advanced tracks are deferred until the requested four-section implementation is complete and separately reviewed. This does not mark any existing gate passed.

Current Phase 5C local input supersedes historical half-duplex defaults: continuous capture uses a playback-reference WebRTC AEC and epoch-safe near-end interruption. `--half-duplex` retains the software-muted diagnostic, while response buffering is independent. Phases 5A–5C are explicitly authorized advanced work without waiving native gates; delivery is one local commit per phase and **no push**, superseding the standing publishing rule for these phases. See [Level 7](levels/level-07.md#phase-5c-local-full-duplex-and-echo-cancellation).

## Current explicit scope — sections 41–46

The owner explicitly authorizes sections 41–46 after published sections 34–40 (`adfb5bf`), including implementation before earlier native/full gates close. [Level 10](levels/level-10.md) owns sections 41–44; [Level 11](levels/level-11.md) owns only sections 45–46. Defaults remain legacy cloud speech with RAG/recording independently disabled. Final combined-source verification is pending, and native/quality/capacity/privacy gates stay open. Latest owner instruction renews commit and push for completed sections 41–46, superseding earlier local-only publication. This scope does not authorize sections 47–48, working database/private setting changes, paid/live calls, microphone collection, downloads or policy sign-off.
