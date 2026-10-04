# Level 9 — Governed retrieval

**v4 sections:** 32–40. **Status:** Planned; no governed RAG implementation.

## Entry gate

Text/local-model foundation, Level 8 governance/access and reliable job execution.

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
- [ ] Local embeddings and no-evidence deferral work without external AI calls.

## Boundaries and advanced work

No RAG on every turn, ungoverned uploads, memory guesses, separate vector database or retrieval-controlled FSM.

## Evidence

Record revision, reproducible commands, environment/data/configuration identity, actual outcomes, demo/reviewer result and limitations here when this level is implemented. Existing foundations do not automatically pass the full gate. Use the [verification contract](../12-verification.md), [baseline](../roadmaps/level-00-baseline.md) and [decision register](../decisions.md).

Return to the [documentation index](../README.md).
