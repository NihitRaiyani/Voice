# Engineering decisions and owner questions

These decisions adapt the OpenAI roadmap to the actual `Voice_Agent` repository. They preserve implemented foundations and distinguish source presence from verified level completion.

## Accepted decisions

| ID | Decision | Engineering consequence |
|---|---|---|
| A01 | `Voice_Agent` is the working project; the OpenAI Word roadmap supersedes v4 | Verify repository root; no work in Weltec for this task |
| A02 | Implement one explicitly requested level or section at a time | Current scope implements sections 18–19 ASR scoring/evidence and isolated local TTS/cache; required TTS downloads explicitly approved on 2026-10-19; preserve earlier foundations and keep native/production approvals separate |
| A03 | Retain layered `roma/` modular monolith and backend-only surface | Keep the existing package and backend-only scope |
| A04 | Retain Twilio/Pipecat cloud comparison path | Keep the existing Twilio adapter and OpenAI LLM; local speech changes require later gates |
| A05 | PostgreSQL durable authority, Redis transient state/cache/delivery | Reuse existing migrations/repositories/jobs; complete missing live integration |
| A06 | Code owns stages, dates, booking, safety, consent and hang-up | Models supply wording/extraction; never business authority |
| A07 | Live Hindi-base Hinglish; understand three languages/code-mix | User confirmed separate multilingual-output lab |
| A08 | Provider contracts first, OpenAI LLM and direct local speech inference | OpenAI quality/latency/budget and Indic speech/embedding quality/hardware/licenses require evidence |
| A09 | Conditional governed RAG at L9, local embeddings and pgvector exact first | Booking/control/safety bypass retrieval; no separate vector DB by default |
| A10 | Full Docker packaging at L13; L14 optional | Existing Compose is compatibility tooling, not foundation requirement or gate pass |
| A11 | OpenAI is the only permitted external GenAI API in the target | Keep OpenAI for the LLM; replace cloud speech only after measured local ASR/TTS gates |
| A12 | Short booking commit permitted in live turn; external sync/recording conversion asynchronous | No transaction spans inference/audio or network waits |
| A13 | Reuse atomic answer receipt/call/event and durable job ledger | Complete remaining security/governance controls rather than rebuilding these paths |
| A14 | One recording consumer; separate row-claimed Dramatiq concurrency | Startup inflight recovery is not multi-consumer safe |
| A15 | Selected recording source is local stereo capture | Pending consent still blocks retention; carrier recording is separate work |
| A16 | Level brief owns acceptance evidence; topic docs own shared contracts | No recurring handoff/log/session journals or competing active plans |
| A17 | Bound pool settings and budget peak including overflow across all DB-owning processes | Explicit allocation validation plus real-server capacity preflight; preserve existing 5/10/5 defaults |
| A18 | Reuse existing durable entities/migrations and short units of work for L1 section 3 | Add pressure/timeout/cancellation evidence and reject nested active UoW reuse; live turn audit integration stays with L2/L7/L8 |
| A19 | Add conversation checkpoint, model registry and benchmark schema through `20261004_0003` | Preserve old migrations/keys/semantics; add nullable turn language with no guessed historical backfill |
| A20 | Checkpoint ownership cascades from call; benchmark results cascade from run and restrict model deletion | Explicit checkpoint expiry; non-PII benchmark metadata only; restoration and cleanup remain later-level work; sections 18–19 add completed-benchmark writers |
| A21 | Preserve all three revisions; database commands need only database settings; synthetic seeds are separate and explicit | No renumbering or empty revision for section 5; dev/test-only head-checked atomic seeds preserve existing data; rollback guidance identifies lost data and compatible code |
| A22 | Automatically commit and push after each completed user-requested level or section | Standing user authorization; verify checks and synchronize docs first, preserve unrelated edits/secrets, report the GitHub commit, never force-push or start the next level implicitly |
| A23 | Treat `roma/` as the v4 backend app package rather than renaming to `backend/app` | Preserve import stability; enforce module ownership and domain dependency direction with tests |
| A24 | Provider contracts precede model/runtime integration | STT/LLM/TTS/embedding/telephony callers depend on protocols and mocks first; local/cloud adapters must pass their later level gates before use |
| A25 | Versioned REST resources coexist with legacy dial commands | `/api/v1` exposes durable business resources with envelopes; `/api/call` remains the explicit carrier-side-effect path until a later client migration |

| A26 | The OpenAI source supersedes the local-LLM plan | No Qwen runtime; local speech/embeddings remain later work; OpenAI is the only permitted target cloud GenAI provider |
| A27 | Quota-safe section 11 validation | Use injected SDK transports/mocks; native scoring, paid latency, full voice approval and live integration remain separate gates |
| A28 | Reuse the deterministic filter and existing safety schema for section 12 | Versioned rule activations, no raw transcript in audit; short bounded writes before forwarding speech; audit failures use a safe fallback and explicit failure signal; native review stays pending |
| A29 | Hosted HF ASR is an explicit lab exception to the local-speech target | `hf_public_lab` uses only declared public benchmark audio, never private caller audio; anonymous defaults remain, with the later user-authorized official Whisper edge authentication exception; Gujarati/Hindi use the selected Indic Space and English the official Whisper Space. The authenticated router is separately selected, with no fallback/retry on 402 or automatic credit purchase/deployment. Three successful public clips prove access, not native quality or a Level 4 pass. |

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
| D5 | Local speech/embedding GPU budget/licenses, OpenAI model/budget and approved evaluation corpus — engineering/institute owner | L2/L4/L5/L10; candidates unselected until benchmark/license review |
| D6 | Production TLS origin/host, secrets, RTO/RPO and backup ownership — operations owner | L13; no host or recovery objective implied |
| D7 | Roles/session/revocation, provider replay and token URL exposure policy — security/institute owner | L8; signature/idempotency do not complete user auth or replay policy |
| D8 | Measured quality/latency/capacity SLOs — product/engineering owner | L12; roadmap numbers remain provisional |
| D9 | Direction-specific opener/prompts and truthful committed confirmation — conversation/engineering owner | L3/L10; shared cloud wording is not local/live acceptance evidence |

D3 in older source comments refers to Gujarati-script recognition observations. Preserve first-class Indic matching; that historical label is not evidence of local ASR quality.

## Remaining engineering gaps

Section 13 connects transactional booking and same-SID appointment reconciliation. Provider-neutral local AI, conditional RAG, user/RBAC/replay/privacy controls, publication/dead-letter bounds, complete durable telemetry, broader restore drills and measured capacity remain open at their respective levels. Level 2 section 10 now provides the durable latest conversation checkpoint adapter; the new OpenAI provider/text seam is section 11; section 12 provides structured safety audit in the text and existing pre-TTS paths, with protected query endpoints. Full local-stack voice approval and native quality remain pending. Fresh verification/limitations are in [baseline evidence](roadmaps/level-00-baseline.md).

## Section 13 booking decisions

Retain the existing `booked`/`confirmed` unique index and matching-slot FK; no migration is justified. Use short pessimistic slot locks with the constraint as final authority. Add advisory 90-second owner-token Redis holds outside SQL transactions, stable booking IDs for safe retries, and atomic cancellation/slot release. Commit before controller win or confirmed speech; restore same-SID appointment truth from PostgreSQL. Preserve the existing IST hours/90-minute lead/14-day horizon, capacity-one semantics and Hindi-base Hinglish policy. The static calendar remains an explicit offline baseline. Full policies, failure trade-offs and evidence: [Level 3](levels/level-03.md).

## Section 14 audio decisions

Move the unchanged pure-Python G.711 implementation inward and preserve its legacy import/asset contract. New streaming boundaries reject half-samples. Use an explicit, stateful, anti-alias FIR reference converter and bounded 20 ms frames. Default the offline fixed buffer to 60 ms (40–60 allowed) with virtual-clock impairment tests and explicit discontinuities, no PLC. Keep the live native codec/current rates; add pre-decode Twilio validation and media-chunk gap counts without a new TCP playout delay. Global control-message sequence gaps are not media loss. Adaptive jitter/PLC and real RTP impairment/ASR measurements wait for the full Level 4 gate. Scope and evidence: [Level 4](levels/level-04.md).

## 2026-10-18 — Sections 15–17 speech decisions

Keep Silero's bundled ONNX probabilities and own endpoint timers/hysteresis in domain code. Reframe 20 ms PCM input into 32 ms model windows; unknown gaps reset state and never count as silence. Retain the production cloud path until measured local quality/integration gates pass.

The owner permits HF-hosted ASR to avoid local weights. Provide an explicit opt-in custom endpoint adapter while retaining the self-hosted speech target. A token does not provision compute; no serverless provider was listed for the selected Indic model on inspection, so no endpoint, payment, download or live inference was performed. Cache-only local IndicConformer and faster-whisper comparison adapters preserve the curriculum's direct model seam. IndicConformer requires reviewed pinned custom code/offline loading; explicit English requires the comparison/retained baseline. The OpenAI LLM and production reply policy remain unchanged.

Persist deterministic caller-language evidence in the existing checkpoint JSON: five routes, bounded label history, two-observation switches and one accepted-turn hook. Heuristic/interface evidence is separate from native ASR/noise/quality approval. [Level 4](levels/level-04.md#sections-1517-design-and-acceptance) owns exact defaults, verification and limitations.

## 2026-10-19 — Sections 18–19 measurement and local synthesis

Use reproducible public ASR data and explicit noise/telephone transformations before changing production speech. Preserve per-case failures and weighted edit totals; compare models on the same manifest. Native code-switch recordings and human approval are separate from stitched public read speech. Hosted timings include network/queueing and must not be presented as isolated compute RTF. Save completed benchmark evidence into existing tables with short transactions and immutable run identities; schema changes are unnecessary.

The owner explicitly approved required local TTS runtime and weight downloads. Isolate the legacy Coqui runtime from app dependencies, take FastPitch/HiFi-GAN/speaker artifacts from the official AI4Bharat release, record hashes and fail on incompatibility. No automatic inference-time downloads, paid fallback or live speech switch. The bounded local phrase cache always follows safety checks and includes language/voice/model/text/normalization/format identity. Exact model/runtime decisions, actual inference outcomes and remaining review are owned by [Level 5](levels/level-05.md).


## 2026-10-19 — Scoped hosted Whisper and safety pronunciation follow-up

Keep the existing Whisper endpoint selection; an alternate official Space also exhausts public ZeroGPU quota. Correct same-job detailed queue handling and explicit API-name configuration, sanitize failures and retain failed comparison evidence. Available hosted capacity remains required; no retry loop, token disclosure, paid provisioning or local ASR download is authorized by this correction.

Preserve all canonical safety wording and live assets. Bind only the six distinct approved lines to literal/hash-pinned Hindi-base Hinglish Devanagari pronunciation. Audit canonical decisions plus exact pronunciation identity; independently screen actual speech, with a sole-CERT adjudication restricted to the exact pinned certificate deferral. Use the Hindi model/cache identity across caller routes. Actual synthesis passes 24/24 cases; native listening and complete live voice approval remain separate.


## 2026-10-19 — Official Whisper authentication and section 20

The user explicitly authorizes the `.env` token for official Whisper inference without downloads. Use the standard Gradio HF edge header only for the reviewed official hosts, with explicit opt-in and no secret logging/redirect/proxy handling. Keep anonymous/community routes unchanged. Authentication produced seven successful corpus cases before account quota exhausted; the router still requires unavailable credits. Stop at the first future quota rejection, preserve reports and never buy/deploy capacity automatically.

Implement section 20 as an isolated half-duplex local composition, with mocks by default, optional PortAudio dependency, final safety and durable receipt/ready/result writes. Reuse SQL booking/Redis holds and state checkpoints; no schema change or model installation. Exact native pronunciations preserve canonical controller templates and validated values. Hindi-base Hinglish remains the output policy. All-local microphone mode rejects hosted ASR; missing local ASR runtimes/weights and unfinished native entry gates prevent claiming full Level 6 acceptance.


## Approved local Whisper base follow-up — 2026-10-19

The owner lifted the no-download restriction only for multilingual Whisper base. Use the pinned four-file converted snapshot with faster-whisper CPU INT8 in the isolated lab; no larger/Indic ASR downloads, hosted capacity purchase or production switch. Run all 15 cases with a distinct report/model/run identity; never combine base with hosted large-v3 results. Execution completed 15/15, but Gujarati/Hindi/stitched quality failed. Preserve the primary IndicConformer roadmap requirement and native gates. OpenAI GPT-4o remains the LLM; use process-scoped lab settings rather than rewriting `.env` or production defaults. [Level 4](levels/level-04.md#local-whisper-base-baseline--2026-10-19) owns ASR evidence; [Level 6](levels/level-06.md) owns the real microphone attempt.

Owner steering during the local-base follow-up: **do not use OpenAI now; quota is exhausted**. Further microphone checks use explicit `--llm-provider mock` with real local ASR/TTS; no OpenAI settings/client construction or paid opt-in. OpenAI stays the main production LLM for later authorized testing. A mock-LLM hardware result cannot pass the real OpenAI conversation gate.

The repaired physical English microphone/local-TTS turn completed and was heard clearly by the owner with OpenAI disabled. Missing booking reference data still selects the existing approved re-offer; no seed or business-rule changes were used to force a pass. This hardware result does not pass multilingual/booking/OpenAI/native gates.
