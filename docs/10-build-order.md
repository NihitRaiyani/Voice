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

Current authorized task: align implementation and documentation through section 20 with the [Claude source](roadmaps/README.md). Claude Haiku 5.5 replaces OpenAI for wording/extraction. Earlier synthetic paid verification remains historical. The owner resumed a bounded paid Claude/microphone follow-up on 2026-10-22; see Level 6 for actual outcomes and remaining limits. Tests and the synthetic prototype remain mocked. This does not waive unfinished teacher/native gates or expand into section 21. No OpenAI requests or automatic provider fallback.

Current local development ASR is pinned Turbo MLX 4-bit with structural batching and reference-compatible waveform padding. [Level 4](levels/level-04.md#waveform-padding-repair-and-model-retirement--2026-10-22) owns the 15-case rerun and authorized full-v3 cache retirement. [Level 6](levels/level-06.md#supervised-claude-and-booking-follow-up--2026-10-22) owns real four-language-labelled input checks, owner clarity/relevance approval, the consent repair and confirmed TEST ONLY microphone booking/SQL restore. No downloads, model cascade or production-provider change occurred in that follow-up. Formal native/representative and voice latency gates remain pending; no section 21 work is implied.
