# v4 level progression and gate policy

Develop Levels 0–14 from the selected source. Introductory prose says 0–13, but its table/body include optional Level 14. Engineering levels are distinct from Roma's seven runtime conversation stages.

| Levels | Outcome |
|---|---|
| 0–1 | Baseline/prerequisites, durable backend, contracts/mocks, versioned API and CI foundation |
| 2–3 | Direct local text inference, deterministic safety/state and truthful transactional booking |
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

Provider interfaces precede local integrations, direct inference precedes vLLM, RAG begins at Level 9 and full Docker packaging begins at Level 13. Retain existing PostgreSQL/Redis Compose as pre-v4 compatibility tooling without making it the native foundation requirement.

Latest authorized task: Level 1 section 9 testing/linting/native startup and CI skeleton, preserving sections 3–8 persistence/pool, relational foundations, migration workflow, architecture boundaries, provider contracts and versioned REST resources. Level 0 gate remains pending; later levels have varying existing foundations and missing acceptance work. See [readiness](13-backend-roadmap.md).
