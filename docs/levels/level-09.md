# Level 9 — Governed retrieval

**Roadmap sections:** 32–40. **Status:** Routing/governance plus sections 34–40 ingestion, bounded chunking, pinned local embeddings, exact pgvector search, extractive grounded answers and golden evaluation are implemented. Actual local embedding/retrieval evidence is recorded below; real Claude quality, representative/native acceptance and the full Level 9 gate remain open. Runtime RAG defaults off.

## Entry gate

Claude text and local speech foundation, Level 8 governance/access and reliable job execution.

## Implemented scope

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

Small approved knowledge remains an exact static lookup from the existing reviewed course/stage facts, with no new facts inferred. Facts are emitted once within the existing course scope; unmatched or unsupported topics use the approved no-evidence response. Sections 32–33 introduced no embedding model or search. Sections 34–40 now supply the separately opt-in retrieval pipeline below; its golden evidence does not authorize production traffic.

## Section 33 governance and evidence metadata

New SQL knowledge_documents, knowledge_chunks and retrieval_logs record governance before search. Documents carry owner, uploader, source/reference, immutable version/content hash, category, language, course/branch scope, effective dates and server-recorded approval provenance. Only existing human Admin permissions can upload, approve, activate or archive; owner metadata does not replace RBAC. Strict bounded JSON rejects unsupported categories, recognized private identifiers, protected claims and instruction-override content.

The lifecycle is draft -> approved -> active -> archived. Approved payloads cannot be edited into a different claim: the approved hash binds the document metadata/content, and SQL guards protect immutable approved versions. Eligibility rechecks active status, actual approval, content identity, effective dates and language/course/branch scope; stale, changed, inactive or out-of-scope evidence fails closed. Server approval never comes from a client-supplied approved flag.

The original section 33 chunk contract carried bounded text/language/provenance and nullable future embedding identity. Sections 34–40 now populate verified local-model/revision/dimension/preprocessing/artifact and pipeline identities with pgvector values; the original governance boundary remains. Retrieval metadata supports call/turn/chunk references, bounded scores/latency, route/outcome and fallback status, without raw query/transcript/personal fields. The original transcript remains under the private call-data/retention boundary rather than copied into retrieval logs. This was the section 33 eligibility/governance foundation; the ingestion/search/answer contracts below extend it.

Migration 20261024_0006 appends these three tables after privacy 0005, preserving earlier revisions. Offline tests cover RBAC, lifecycle/immutability, effective dates/scopes, metadata privacy, and disposable PostgreSQL migration behavior. Full Level 9 retrieval/golden-set/native acceptance remains pending.

## Sections 34–40 ingestion and local retrieval

Admin JSON upload now records managed ingestion intent with the document; repeated document/hash/pipeline requests reuse one durable job. The explicit SQL worker drains a bounded page rather than running inference in the upload request. SQL owns a 300-second claim lease, attempt fencing, at most five attempts, delayed transient retries, safe reason codes and dead letters/quarantine. Crash recovery reclaims expired work; a stale worker cannot publish over its successor. Inference runs outside transactions, and publication rechecks original content plus canonical chunk derivation. The Dramatiq actor remains an injectable ID-delivery seam; the documented operator path is the SQL drain.

Plain text/Markdown is normalized to NFC, bounded to 20,000 characters and divided into at most 100 chunks of at most 2,000 characters. Section context and table headers accompany complete ordered rows; oversized indivisible rows fail. Instruction-like content is rejected/quarantined, never executed. Approval stays human Admin-owned. Managed activation requires completed matching ingestion and nonempty identity-consistent vectors; this quality fence proves pipeline integrity, not native or semantic approval. Archive/effective dates/approved hashes and immutable versions remain authoritative.

Local embeddings use `intfloat/multilingual-e5-small` revision `614241f622f53c4eeff9890bdc4f31cfecc418b3`, 384 dimensions, `e5-prefix-mean-l2-v1` and a SHA-256 artifact manifest. Loading is cache-only, safetensors-only, CPU-bound, with no remote code or cloud fallback. Query/passages have distinct prefixes, masked mean pooling and L2 normalization; input/token/batch/thread bounds and finite/unit-vector checks fail closed. Cancellation/timeout retains native admission until the worker actually exits. The owner approved provisioning if RAM fit: the isolated process measured about **807 MB on an 8 GiB host**; simultaneous ASR/TTS/embedding headroom remains unproven.

Migration `20261026_0007` appends vector columns, ingestion jobs and activation guards after `0006`. It requires the PostgreSQL `vector` extension and installs no model/data. It was exercised only on disposable databases for this increment; working `roma` remains at its previously approved `0006`. Downgrade discards this increment's vector/job data and preserves the shared extension.

Exact cosine search applies active/approved/hash/effective-date/category/language/course/branch/optional-version and completed-ingestion/model-identity predicates before ranking. English fallback is an explicit filter option; production resolves one active approved course/branch and searches the spoken-output evidence policy. Canonical chunk derivation and payload identity are checked again after SQL selection and around generation. There is no approximate index or separate vector database. Defaults are top K **5**, minimum score **0.80** and retrieval deadline **450 ms**.

## Grounded answer release and runtime boundary

Existing approved static answers and controller routing retain priority. Only an eligible factual interruption without a static answer reaches retrieval; discovery, booking, consent, safety and call control retain their existing owners. API/controller/text/prototype composition supports opt-in retrieval with `RAG_ENABLED=false` by default. Caller language never changes production Hindi-base Hinglish output; voice output additionally requires its existing supported spoken script.

Claude receives bounded quoted evidence and must return exactly a chunk ID and contiguous verbatim span, or nulls. V1 releases at most **35 words / 800 characters** after valid completion/schema, selected-chunk identity, exact-substring, output-policy and deterministic safety checks. Evidence is revalidated before generation and before release. The generation deadline defaults to **10 seconds**; epoch cancellation invalidates late work. Durable metadata audit must succeed before releasing a fact, without copying raw caller queries/transcripts into retrieval logs. Unsupported, stale, weak, unsafe or unavailable evidence defers. Exact quotation proves mechanical support, not relevance or semantic entailment; Claude judgment remains an acceptance requirement.

## Golden evaluation and remaining gate

The synthetic [golden corpus](../../benchmarks/rag/golden-v1.json) contains **62 cases**, split into **24 calibration / 38 held-out**. A fixed calibration-only grid selects **0.80**; held-out cases never select the threshold. English, Gujarati, Hindi and Hinglish queries cover course/policy/branch/schedule, unknown facts, scopes/versions, bypasses and hostile topics. The [actual local report](../../benchmarks/rag/local-v1-report.json) records the pinned E5 and exact pgvector execution with corpus/model/material fingerprints. Its held-out Recall@5 and MRR are **93.75%** overall; Hindi is **75%**, and English/Gujarati/Hinglish are **100%** on four answerable held-out cases per language. The Hindi batch-schedule miss appears in calibration and held-out failures.

Warm retrieval P50/P95/P99 are approximately **13.09 / 14.03 / 14.09 ms**. These include query normalization, embedding and SQL search only, excluding artifact loading, generation, evidence revalidation and audit. Span support, expected deferral, routing and safety mechanics are **100%** in the held-out fixture run. Its deterministic fixture selector is an oracle for release checks, **not real Claude quality evidence**: `real_claude_groundedness` is null, no paid Claude/native review ran, and `quality_gate_passed` is false.

Owner-approved provisional aggregate targets are Recall@5 **80%**, MRR **70%**, groundedness **90%**, expected deferral/safety **95%** and retrieval P95 **450 ms**. Mechanical targets pass in this small synthetic run; exact-span support cannot fill the real-groundedness gate. Before enabling accepted production traffic, obtain approved representative documents/corpus, resolve the Hindi retrieval weakness, measure real Claude semantic grounding/relevance and complete answer latency, confirm combined voice-stack RAM and cancellation/concurrency, and record native/reviewer approval. Existing speech/privacy gates remain separate. Reproduction and worker/API steps live in the [runbook](../runbook.md#governed-ingestion-retrieval-and-golden-evaluation-sections-3440); final combined offline checks are owned by [verification](../12-verification.md#sections-3440-governed-retrieval--2026-10-26).

Combined verification: `make ci` and `make test` each passed **3,933 tests**, Ruff and mypy (**155 files**) passed, and the complete **222-module** catalog plus migration upgrade/schema parity/downgrade/re-upgrade checks ran. [Verification metadata](../../benchmarks/rag/verification-20261026.json) records source identity and minimized SQL readback; native results were saved as one benchmark run and 45 metric rows, with an ignored protected synthetic dump retained after disposable services stopped. These are offline/local-lab proofs; the full Level 9 acceptance gate remains open.
