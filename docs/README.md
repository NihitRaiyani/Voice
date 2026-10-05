# Voice_Agent documentation

This is the project's canonical documentation entry point. v4 defines future development; actual source and verification define present capability. Implementation status is recorded in each level brief; Level 1 sections 3–9 cover persistence/pool safeguards, relational foundations, migration/separate-demo workflow, backend architecture boundaries, provider contracts, versioned REST resources and the day-one testing/CI skeleton.

## Level briefs

Read the [progression/gate policy](10-build-order.md) and [current readiness](13-backend-roadmap.md), then the requested brief.

| Level | Brief | Current gate position |
|---|---|---|
| 0 | [Baseline/prerequisites](levels/level-00.md) | Assessment recorded; gate pending |
| 1 | [Backend foundation](levels/level-01.md) | Sections 3–9 foundations implemented; hosted CI/full review evidence follows the workflow commit |
| 2 | [Text/local LLM](levels/level-02.md) | Cloud controller present; local/durable gate pending |
| 3 | [Transactional booking](levels/level-03.md) | Repository present; live/API integration pending |
| 4 | [Audio/local ASR](levels/level-04.md) | Cloud baseline; local lab planned |
| 5 | [Local TTS](levels/level-05.md) | Cloud cache; local lab planned |
| 6 | [Local voice prototype](levels/level-06.md) | Planned |
| 7 | [Real-time reliability](levels/level-07.md) | Cloud seams; local-stack gate pending |
| 8 | [Secure jobs/access](levels/level-08.md) | Signed answer/durable jobs present; full gate pending |
| 9 | [Governed retrieval](levels/level-09.md) | Planned |
| 10 | [Telephony/model serving](levels/level-10.md) | Twilio cloud baseline; local integration planned |
| 11 | [Observability/analytics](levels/level-11.md) | Basic logs; full telemetry planned |
| 12 | [Evaluation/capacity](levels/level-12.md) | Offline foundation; production report pending |
| 13 | [Container deployment](levels/level-13.md) | Pre-v4 infrastructure only; full packaging planned |
| 14 | [Optional optimization](levels/level-14.md) | No experiment selected |

## Shared engineering contracts

| Document | Owns |
|---|---|
| [00 Charter](00-project-charter.md) | Product/learning outcomes and scope |
| [01 Architecture](01-architecture.md) | Current/target ownership and dependency direction |
| [02 Pipeline](02-pipeline.md) | Turn flow, safety and timing |
| [03 Stage machine](03-stage-machine.md) | Deterministic transitions, confirmation and booking truth |
| [04 Guardrails](04-guardrails.md) | Final speech policy and evidence limits |
| [05 Audio/endpointing](05-endpointing-vad.md) | Codec, language, VAD and interruption measurement |
| [06 State/cache](06-state-and-cache.md) | Datastore authority, lifetimes, pools and recovery |
| [07 Security](07-security.md) | Auth/callback/PII/consent boundaries |
| [08 Concurrency](08-concurrency.md) | Queues, cancellation, consumers and delivery |
| [09 Recordings](09-recording-storage.md) | Capture, consent, durability and ack |
| [11 Prompts](11-prompts.md) | Runtime wording/extraction versus code authority |
| [12 Verification](12-verification.md) | Evidence rules and actual check limitations |
| [14 Data/concurrency](14-data-and-concurrency.md) | Existing constraints and live-booking gap |
| [15 API/security/jobs](15-api-security-and-jobs.md) | Compatibility/versioned API and identities |
| [24 REST API design](24-rest-api-design.md) | v1 resource routes, envelopes, pagination/filtering/sorting and OpenAPI |
| [16 Observation/delivery](16-observability-testing-and-delivery.md) | Metrics, capacity, CI and packaging |
| [17 Study guide](17-placement-study-guide.md) | Concept-to-module learning and viva |
| [18 Webhooks](18-webhook-idempotency.md) | Implemented atomic answer acceptance |
| [19 Worker framework](19-background-task-framework.md) | Implemented Dramatiq/SQL job ownership |
| [20 Relational schema](20-relational-schema.md) | Complete table/key/constraint/index rationale, cascade and retention choices |
| [21 Database migrations](21-database-migrations.md) | Preserved revision history, forward workflow, rollback impact and separate opt-in demo seeds |
| [22 Backend architecture contract](22-backend-architecture-contract.md) | Modular monolith map, dependency direction and section 6 boundary enforcement |
| [23 Provider contracts](23-provider-contracts.md) | STT/LLM/TTS/embedding/telephony interfaces, mocks and config switching |

## Decisions, source and operation

- [Decision register](decisions.md): adopted choices, owner decisions and known gaps.
- [Runbook](runbook.md): baseline setup, migrations, workers and recovery procedures.
- [v4 source mapping](roadmaps/README.md): unchanged Word file and section-to-level coverage.
- [Baseline evidence](roadmaps/level-00-baseline.md): source/environment identity and fresh results.
- [Completion contract](completion-contract.md): nine mandatory areas, final demo and student deliverables.
- [Historical archive](archive/README.md): pre-v4 references, not active build instructions.

Runtime prompts under `roma/prompts/` affect speech and are not edited as ordinary prose. [Asset guidance](../roma/realtime/assets/README.md) governs matching fixed audio. [Project skill guidance](../skills/README.md) follows these contracts.
