# Engineering decisions and owner questions

These decisions adapt v4 to the actual `Voice_Agent` repository. They preserve implemented foundations and distinguish source presence from verified level completion.

## Accepted decisions

| ID | Decision | Engineering consequence |
|---|---|---|
| A01 | `Voice_Agent` is the working project; v4 replaces the prior curriculum | Verify repository root; no work in Weltec for this task |
| A02 | Implement one explicitly requested level or section at a time | Current scope is L1 section 5 migration workflow, preserving sections 3–4 foundations; other sections, live calls and deployments require their own scope |
| A03 | Retain layered `roma/` modular monolith and backend-only surface | Keep the existing package and backend-only scope |
| A04 | Retain Twilio/Pipecat cloud comparison path | Use the existing Twilio adapter through the v4 migration |
| A05 | PostgreSQL durable authority, Redis transient state/cache/delivery | Reuse existing migrations/repositories/jobs; complete missing live integration |
| A06 | Code owns stages, dates, booking, safety, consent and hang-up | Models supply wording/extraction; never business authority |
| A07 | Live Hindi-base Hinglish; understand three languages/code-mix | User confirmed separate multilingual-output lab |
| A08 | Provider contracts first, direct local inference before vLLM | Candidate Qwen/Indic quality/hardware/licenses require evidence |
| A09 | Conditional governed RAG at L9, local embeddings and pgvector exact first | Booking/control/safety bypass retrieval; no separate vector DB by default |
| A10 | Full Docker packaging at L13; L14 optional | Existing Compose is compatibility tooling, not foundation requirement or gate pass |
| A11 | Final local production makes zero external GenAI API calls | Keep cloud comparison profile explicit during migration |
| A12 | Short booking commit permitted in live turn; external sync/recording conversion asynchronous | No transaction spans inference/audio or network waits |
| A13 | Reuse atomic answer receipt/call/event and durable job ledger | Complete remaining security/governance controls rather than rebuilding these paths |
| A14 | One recording consumer; separate row-claimed Dramatiq concurrency | Startup inflight recovery is not multi-consumer safe |
| A15 | Selected recording source is local stereo capture | Pending consent still blocks retention; carrier recording is separate work |
| A16 | Level brief owns acceptance evidence; topic docs own shared contracts | No recurring handoff/log/session journals or competing active plans |
| A17 | Bound pool settings and budget peak including overflow across all DB-owning processes | Explicit allocation validation plus real-server capacity preflight; preserve existing 5/10/5 defaults |
| A18 | Reuse existing durable entities/migrations and short units of work for L1 section 3 | Add pressure/timeout/cancellation evidence and reject nested active UoW reuse; live turn audit integration stays with L2/L7/L8 |
| A19 | Add conversation checkpoint, model registry and benchmark schema through `20261004_0003` | Preserve old migrations/keys/semantics; add nullable turn language with no guessed historical backfill |
| A20 | Checkpoint ownership cascades from call; benchmark results cascade from run and restrict model deletion | Explicit checkpoint expiry; non-PII benchmark metadata only; restoration/runner/cleanup consumers remain later-level work |
| A21 | Preserve all three revisions; database commands need only database settings; synthetic seeds are separate and explicit | No renumbering or empty revision for section 5; dev/test-only head-checked atomic seeds preserve existing data; rollback guidance identifies lost data and compatible code |
| A22 | Automatically commit and push after each completed user-requested level or section | Standing user authorization; verify checks and synchronize docs first, preserve unrelated edits/secrets, report the GitHub commit, never force-push or start the next level implicitly |

## Preserved operational invariants

Store lead context before Twilio dialing; check public reachability before consuming hourly allowance. Fail before dialing if required lead storage fails. Cosmetic status-store failure after accepted dialing must not report the dial as failed. Outbound direction follows custom lead-parameter presence even on Redis degradation.

Twilio HTTP/WS signature and start Account SID checks remain. Stream lead metadata uses custom parameters; the current answer callback URL still has a sensitive lead query token requiring log/proxy protection. Final safety/cancellation, Indic normalization, fixed-audio identity, ordered discovery, stage caps and the five-minute ceiling remain.

Answer receipt/call/event commit together with a three-second acceptance bound; unavailable persistence is 503, changed-input identity is 409. Independent receipt retention prevents replay from recreating deleted calls. Redis status is a best-effort monotonic projection. Neither transaction guarantees exactly-once remote execution.

Post-call storage/handoff precedes exact-payload ack. PostgreSQL owns job business retries/leases/effects; Dramatiq carries ID notifications and delivery retries. Follow-up intent exists but sending is inactive. See [webhooks](18-webhook-idempotency.md) and [jobs](19-background-task-framework.md).

## Owner decisions and gates

| ID | Decision / owner | Gate and safe default |
|---|---|---|
| D1 | Approved course/certificate facts — Weltec academic/business owner | Safety/L9; Weltec certificate only until approval |
| D2 | Disclosure wording/audio, contact consent, retention/deletion/backup policy — institute privacy owner | L8/L10/production; pending marker means no retained audio |
| D4 | Branch visiting hours, slot duration/capacity/cancellation and counsellor policy — counselling owner | L3; current active uniqueness supports one appointment per slot |
| D5 | Local-model/GPU budget, licenses and approved evaluation corpus — engineering/institute owner | L2/L4/L5/L10; candidates unselected until benchmark/license review |
| D6 | Production TLS origin/host, secrets, RTO/RPO and backup ownership — operations owner | L13; no host or recovery objective implied |
| D7 | Roles/session/revocation, provider replay and token URL exposure policy — security/institute owner | L8; signature/idempotency do not complete user auth or replay policy |
| D8 | Measured quality/latency/capacity SLOs — product/engineering owner | L12; roadmap numbers remain provisional |
| D9 | Direction-specific opener/prompts and truthful committed confirmation — conversation/engineering owner | L3/L10; shared cloud wording is not local/live acceptance evidence |

D3 in older source comments refers to Gujarati-script recognition observations. Preserve first-class Indic matching; that historical label is not evidence of local ASR quality.

## Remaining engineering gaps

Live transactional booking, durable conversation restore, provider-neutral local AI, native startup/expanded CI, conditional RAG, user/RBAC/replay/privacy controls, publication/dead-letter bounds, complete durable telemetry and measured capacity remain open at their respective levels. Fresh verification/limitations are in [baseline evidence](roadmaps/level-00-baseline.md).
