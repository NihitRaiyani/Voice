# Level 9 — Governed retrieval

**Roadmap sections:** 32–40. **Status:** Section 32 deterministic routing implemented; governance, ingestion, embeddings/search and the full gate remain separate.

## Entry gate

Claude text and local speech foundation, Level 8 governance/access and reliable job execution.

## Scope when implementation is requested

Route only eligible factual questions to retrieval; booking, safety, consent and call control bypass it. Govern approved documents with active/effective status, course/branch/language/topic scope, version and owner approval.

Build idempotent ingestion/chunk metadata jobs and local multilingual embedding contracts. Start with exact pgvector search; add approximate indexing only after measuring need. Apply scope/status/date/version filters and keep evidence separate from instructions.

Generate grounded answers or approved no-evidence deferral. Evidence cannot unlock blocked topics. Recheck final speech safety and cache eligibility/version. Build a multilingual golden corpus and failure cases.

## Existing reuse in Voice_Agent

Approved facts/direct routing, final safety and existing PostgreSQL/worker boundaries.

## Acceptance gate

- [ ] Eligible versioned documents ingest idempotently; inactive/stale/out-of-scope evidence cannot answer.
- [ ] Golden Recall@K/MRR, grounding, fallback, safety and P50/P95/P99 are recorded.
- [ ] Injection/blocked-topic tests remain safe even when documents contain prohibited claims.
- [ ] Local embeddings and deterministic no-evidence deferral need no cloud embeddings; eligible grounded wording uses Claude under its budgets.

## Boundaries and advanced work

No RAG on every turn, ungoverned uploads, memory guesses, separate vector database or retrieval-controlled FSM.

## Evidence

Record revision, reproducible commands, environment/data/configuration identity, actual outcomes, demo/reviewer result and limitations here when this level is implemented. Existing foundations do not automatically pass the full gate. Use the [verification contract](../12-verification.md), [baseline](../roadmaps/level-00-baseline.md) and [decision register](../decisions.md).

Return to the [documentation index](../README.md).

## Section 32 deterministic knowledge routing

The text service, state-machine facade and live controller classify turns before discovery extraction or knowledge wording. Fixed/stage/booking/protected-topic responsibilities remain code-owned; factual interruptions use the shared controller-only path so global pacing, stage caps and terminal booking/sign-off retain priority. Questions and explicit factual commands cannot populate discovery fields. Retrieval never confirms an appointment, changes consent or bypasses final sentence safety.

| Turn family | Owner | Retrieval decision |
|---|---|---|
| Greeting, exit, approved fixed response | Deterministic controller/cache | None |
| Name/profile answer, discovery or stage progress | Conversation state service | None |
| Booking/rescheduling/cancellation | Existing booking/state transaction path | None |
| Fees, salary, placement, discounts, unapproved certificates | Deterministic safety | None |
| Course curriculum, policy, branch, schedule question | Bounded approved knowledge boundary | Eligible only with matching evidence |
| Unknown/unverified fact | Reviewed counsellor deferral | No model-memory guess |

Small approved knowledge remains an exact static lookup from the existing reviewed course/stage facts, with no new facts inferred. Facts are emitted once within the existing course scope; unmatched or unsupported topics use the approved no-evidence response. No embedding model, vector search, semantic ingestion or generated grounded-answer pipeline is introduced by sections 32–33. Golden-set evidence in later sections will determine retrieval traffic.
