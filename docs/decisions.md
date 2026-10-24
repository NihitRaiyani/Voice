# Engineering decisions and owner questions

These decisions adapt the Claude roadmap to the actual `Voice_Agent` repository. They preserve implemented foundations and distinguish source presence from verified level completion.

## Accepted decisions

Current provider policy was replaced explicitly on 2026-10-20. The active rows below apply Claude; dated entries later in this document preserve earlier OpenAI decisions as historical evidence and are superseded for provider selection.

| ID | Decision | Engineering consequence |
|---|---|---|
| A01 | `Voice_Agent` is the working project; the Claude Word roadmap supersedes the historical OpenAI/v4 sources | Verify repository root; no work in Weltec for this task |
| A02 | Implement one explicitly requested level or section at a time | Current scope aligns existing implementation through section 20; preserve earlier foundations, source/history and native/production approval boundaries |
| A03 | Retain layered `roma/` modular monolith and backend-only surface | Keep the existing package and backend-only scope |
| A04 | Retain Twilio/Pipecat cloud comparison path | Keep the existing Twilio adapter and Claude LLM; local speech changes require later gates |
| A05 | PostgreSQL durable authority, Redis transient state/cache/delivery | Reuse existing migrations/repositories/jobs; complete missing live integration |
| A06 | Code owns stages, dates, booking, safety, consent and hang-up | Models supply wording/extraction; never business authority |
| A07 | Live Hindi-base Hinglish; understand three languages/code-mix | User confirmed separate multilingual-output lab |
| A08 | Provider contracts first, Claude LLM and direct local speech inference | Claude quality/latency/budget and Indic speech/embedding quality/hardware/licenses require evidence |
| A09 | Conditional governed RAG at L9, local embeddings and pgvector exact first | Booking/control/safety bypass retrieval; no separate vector DB by default |
| A10 | Full Docker packaging at L13; L14 optional | Existing Compose is compatibility tooling, not foundation requirement or gate pass |
| A11 | Claude is the only permitted external GenAI API in the target | Keep Claude for the LLM; replace cloud speech only after measured local ASR/TTS gates |
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

| A26 | The Claude source supersedes the local-LLM plan | No Qwen runtime; local speech/embeddings remain later work; Claude is the only permitted target cloud GenAI provider |
| A27 | Quota-safe section 11 validation | Use injected SDK transports/mocks; native scoring, paid latency, full voice approval and live integration remain separate gates |
| A28 | Reuse the deterministic filter and existing safety schema for section 12 | Versioned rule activations, no raw transcript in audit; short bounded writes before forwarding speech; audit failures use a safe fallback and explicit failure signal; native review stays pending |
| A29 | Hosted HF ASR is an explicit lab exception to the local-speech target | `hf_public_lab` uses only declared public benchmark audio, never private caller audio; anonymous defaults remain, with the later user-authorized official Whisper edge authentication exception; Gujarati/Hindi use the selected Indic Space and English the official Whisper Space. The authenticated router is separately selected, with no fallback/retry on 402 or automatic credit purchase/deployment. Three successful public clips prove access, not native quality or a Level 4 pass. |
| A30 | Historical approved IndicConformer comparison; superseded by A31 | Cache revision `2a77d305038dfc502fd9464e7d028b90381976cb`, keep inference offline, pin custom code/config to that snapshot and retain Whisper for English/comparison. Smoke readiness is not model selection; require matched WER/CER/RTF and native review before a production switch. |

| A31 | Keep approved Whisper Turbo MLX 4-bit and remove Indic model weights; whole-turn-only policy superseded by A32 | One local development model for all input routes, offline/cache-only with exact loanwords; 15-case execution is not quality approval. Preserve base comparison/TTS/production/Claude; defer paid and microphone tests. |

| A32 | Allow at most two full-coverage speech chunks in one Turbo batch for mixed turns | Owner-approved relaxation of no-slicing; preserve all PCM, known Indic script and pure-language behavior. One ASR model with compacted decoder rows, no cascade. Native/noisy/latency gates remain pending. |

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
| D5 | Local speech/embedding GPU budget/licenses, Claude Haiku 5.5 low-effort model/budget and approved evaluation corpus — engineering/institute owner | L2/L4/L5/L10; candidates unselected until benchmark/license review |
| D6 | Production TLS origin/host, secrets, RTO/RPO and backup ownership — operations owner | L13; no host or recovery objective implied |
| D7 | Roles/session/revocation, provider replay and token URL exposure policy — security/institute owner | L8; signature/idempotency do not complete user auth or replay policy |
| D8 | Measured quality/latency/capacity SLOs — product/engineering owner | L12; roadmap numbers remain provisional |
| D9 | Direction-specific opener/prompts and truthful committed confirmation — conversation/engineering owner | L3/L10; shared cloud wording is not local/live acceptance evidence |

D3 in older source comments refers to Gujarati-script recognition observations. Preserve first-class Indic matching; that historical label is not evidence of local ASR quality.

## Remaining engineering gaps

Section 13 connects transactional booking and same-SID appointment reconciliation. Provider-neutral local AI, conditional RAG, user/RBAC/replay/privacy controls, publication/dead-letter bounds, complete durable telemetry, broader restore drills and measured capacity remain open at their respective levels. Level 2 section 10 now provides the durable latest conversation checkpoint adapter; the new Claude provider/text seam is section 11; section 12 provides structured safety audit in the text and existing pre-TTS paths, with protected query endpoints. Full local-stack voice approval and native quality remain pending. Fresh verification/limitations are in [baseline evidence](roadmaps/level-00-baseline.md).

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


## 2026-10-20 — Claude source and LLM replacement through section 20

Adopt the unchanged Claude roadmap hash `67a79ba87fbec07051d460fd4ec9d5520f7047c0e242da72a65bfdd09ede5f48`, preserving both former source files. Claude Haiku 5.5 (`claude-haiku-5-5`) with low effort is the main wording/extraction LLM. Use the private existing `ANTHROPIC_API_KEY`, the official async Anthropic SDK, streaming, ephemeral stable-prefix caching, distinct cache-write/read metrics and exact per-model pricing. No temperature/top_p/top_k overrides, OpenAI calls or automatic fallback. Synthetic paid Claude verification is explicitly authorized; ordinary tests and synthetic prototype stay mock/offline. Paid CLI runs require the existing explicit opt-in.

This replaces all earlier “OpenAI remains main” and “OpenAI-only target” policy; it does not rewrite dated reports or claim historical OpenAI tests verify Claude. Preserve code-owned safety/state/booking, Hindi-base Hinglish, the carrier and ASR/TTS/migrations/schema. Native language scores and the full Level 6 conversation remain pending. The pinned Whisper base benchmark executed all 15 cases but failed Gujarati/Hindi/stitched quality; one clear English physical mock-LLM/local-TTS turn is hardware evidence only. [The source audit](roadmaps/sections-01-10-review.md#claude-source-review-through-section-20--2026-10-20) records section-level gaps.


Haiku runtime decision from measured evidence: keep low effort and explicitly disable optional thinking for narrow dialogue/extraction. The initial 40-case run had two empty output-limit completions; do not increase prompt size, silently increase output limits, count empty replies as success or switch models. Empty output uses the approved fallback with actual usage accounting. Numeric schema constraints are transformed by the official Anthropic SDK on the wire and enforced fully by Pydantic in the application. Warm/cached extraction fits the existing 4-second attempt budget; a separate controlled readiness probe is not a new runtime retry policy.

## 2026-10-21 — Local IndicConformer authorization

The owner explicitly authorized the required AI4Bharat IndicConformer runtime and weights after the Whisper base multilingual quality failure. Install only `ai4bharat/indic-conformer-600m-multilingual` revision `2a77d305038dfc502fd9464e7d028b90381976cb`; the snapshot contains 404 files and 2,556,502,929 bytes. The adapter resolves that exact cache with `local_files_only=True`, loads its config from the snapshot and bypasses the upstream custom loader's unpinned Hub lookup. Runtime inference requires `HF_HUB_OFFLINE=1` and explicit reviewed-code opt-in. Gujarati/Hindi cache-only smokes passed; English stays on Whisper. Preserve production routing until the same-corpus WER/CER/RTF and native review justify a change.

## 2026-10-21 — Matched local ASR comparison

Run the same existing 15-case public manifest with pinned local IndicConformer CTC and fresh local Whisper base, preserving historical reports. Treat the three English cases as explicitly unsupported by Indic; do not replace them with another model or invent WER/CER. Compare shared supported cases using weighted edit totals and identical warm case IDs after excluding each process's first success. Persist separate immutable run/model identities and keep high-WER/unsupported public failures.

Measured evidence favors Indic as the Gujarati/Hindi development candidate (shared 33.33% WER / 23.94% CER versus base 100% / 96.91%), with base retained for explicit English/comparison. Stitched English-span deletion prevents a final multilingual quality choice. A language router chooses a hint; it cannot recover English words removed by a language-specific decoder. No production switch, speech-policy change, paid Claude or microphone test is authorized by this comparison.

## 2026-10-21 — Adaptive local code-mix routing

Historical experiment, superseded by the owner-selected single-pass policy below. Its report and immutable SQL evidence are preserved; the phrase-splitting implementation and executable CLI selection have been removed.

Do not build a CTC blank-tail trigger without reviewed token timestamps and calibrated confidence, and do not add an unmeasured frame-level language classifier. For explicit `gu-en`/`hi-en` lab turns, detect at most one strong phrase boundary from bounded PCM, route the base-language head to pinned IndicConformer and let Whisper identify the suffix language. Accept the suffix as English only when detection and credible English text agree; otherwise retain IndicConformer for it. No-gap speech remains Indic-first with an exact booking-loanword map. Provider errors propagate.

The 15-case offline report fixes clean/telephone suffix deletion but fails noisy code-mix and the proposed 1.2-second mixed-turn latency target. Keep this as a development lab path; do not switch production speech or claim native intra-sentential code-mix approval. KenLM, quantization/CoreML, new models and broad audio gain changes require separate measured decisions.

## 2026-10-21 — Single-pass Indic loanwords and explicit English

The owner replaces the phrase-span cascade with one IndicConformer decode for Gujarati/Hindi/mixed turns, followed by exact phonetic booking-word canonicalization at the local STT text boundary. Explicit English uses Whisper base. Remove audio slicing, English-tail arbitration and empty-output fallbacks. Keep Unicode punctuation handling and compound/name protection; do not map native ambiguous date words to English. This policy is shared by the local prototype and offline benchmark; production speech is unchanged.

The same 15 cases complete offline. Mixed median falls from the historical cascade's 2.031 s to 1.148 s, but mixed WER/CER is 44.84% / 37.02%. A transliterated loanword and a deleted English sentence are different failure modes. Adopt the requested simpler development route without claiming a multilingual recognition or microphone gate pass. Preserve historical evidence, keep schema/weights/`.env` unchanged and keep paid Claude/microphone/native checks deferred.

## 2026-10-21 — No-cascade code-mix diagnosis and next evaluation

Do not restore the phrase cascade or force nonblank tokens to manufacture an English transcript. The installed Indic checkpoint yields blank across both probed English tails even before masking, and no head has English alphabet tokens. The cached Whisper base fails the measured prompt/beam alternatives. Runtime remains on the owner-selected single-pass policy while a suitable replacement is evaluated.

Propose one pinned local Whisper large-v3-turbo decode for mixed turns, using the installed faster-whisper runtime and CPU INT8; retain existing pure-language routing pending actual comparison. The additional five-file snapshot is 1.622 GB and has not been approved/downloaded. Evaluate unchanged 15-case WER/CER, English retention and latency plus short loanwords before selecting it. This is a candidate, not an accuracy guarantee; failed settings are retained in [the diagnosis](levels/level-04.md#english-sentence-deletion-root-cause--2026-10-21). Paid Claude and microphone testing stay after the ASR fix.

## 2026-10-21 — Approved Turbo replacement and Indic removal

The owner approved Whisper large-v3-turbo and requested removal of IndicConformer weights. Keep the pinned Apple Silicon MLX 4-bit conversion (464,280,053 bytes); remove the exact Indic cache (2,556,502,929 bytes) and the redundant CPU Turbo trial after hash verification. Preserve shared dependencies, base comparison weights, Indic-TTS, `.env`, Claude, production speech and schema.

Use one whole-turn Turbo decoder plus exact emitted-loanword normalization in the isolated local prototype. Keep mocks by default and inference cache-only. The [15-case evaluation](levels/level-04.md#whisper-turbo-replacement--2026-10-21) completes but fails Gujarati/Hindi and full English-suffix quality; CPU, prompt, timestamp and automatic-language probes do not justify declaring a fix. Retain Turbo only as the owner-selected development baseline. Do not restore the slow cascade or download another model as a hidden fallback. Paid Claude, microphone/native tests and production adoption remain deferred.

## Owner-approved single-model speech batching

The owner explicitly selected “Allow single-model speech-chunk batching”, superseding the earlier no-slicing rule for mixed turns only. Keep the same Turbo 4-bit model, pure-language decoder/settings and production pipeline. Use the strongest natural pause, at most two complete contiguous chunks and one encoder/decoder batch; retain every PCM sample and the known Indic script unless English evidence is strong. Compact finished decoder rows. Do not restore the Indic/Whisper cascade, add a reference prompt or treat a chunk language score as ASR confidence/business authority.

[The 15-case report](levels/level-04.md#single-model-speech-batching--2026-10-21) recovers English output in all mixed cases with five exact suffixes; native/noisy quality and latency remain unapproved. Full-precision Turbo and fused-attention controls were rejected and removed/not adopted. Paid Claude, microphone/native tests, `.env`, TTS, booking, safety, production speech and schema remain unchanged.


## Phase 2 — Prototype turn boundaries

Use the owner's provisional 300 ms continuation grace and 2,000 ms minimum batch turn, configurable through the shared TurnPolicy. Batch eligibility follows a qualifying internal audio gap, including first unexpected language switches; retain the English evidence gate and two-turn saved-route smoothing. Preserve every source sample, flush capture deadlines explicitly and keep the five-minute/30-second limits. This revises the earlier label-only v2 policy without changing weights or production media. See [offline evidence and latency limits](levels/level-06.md#offline-turn-boundary-repair--phase-2). No quality gate is passed.


## Phase 3 — Validate batch output and separate demo readiness

Use independent configurable batch-row rejection at 0.6 no-speech, -1.0 average log probability and 2.4 compression ratio; retain clean rows, stop an entirely rejected turn with its own safe ASR code and preserve ordinary single-call decoding. Measure speech-end to first speaker write independently of full playback, with budgets unset until owner configuration. Explicitly provision TEST ONLY booking data in a disposable native loopback database; reject `roma` and non-dev/test targets. Check selected-branch booking readiness before model/call/device activity. No implicit seed/migration, working-DB write, model change or gate approval. See [configuration and limits](levels/level-06.md#offline-output-and-demo-readiness--phase-3).


## Phase 4 — Representative evaluation scaffolding

Preserve section18 inputs and all historical results. Add a separate consent-aware v2 source-recording manifest, configurable unset minimum distinct speakers/recordings, exact entity references and per-speaker edit totals. Reject duplicate IDs/audio and inconsistent references/permissions. Use supplied-transcript-only report annotation; retain failed recordings in denominators and leave v1 report fields unchanged. Collect no recordings, change no model/ASR strategy and claim no new model score or gate pass. Before collection, the owner must approve counts, coverage/source granularity, entity annotation policy, permissions/storage/retention and reviewers; quality/entity/latency acceptance criteria stay separate. See [the protocol](26-asr-recording-protocol.md).


## 2026-10-22 — ASR repair, cache retirement and bounded live verification

Use reference-compatible waveform padding in the MLX batch frontend. Keep existing rejection thresholds and one selected ASR model, without reference-specific text corrections, retries or cascades. The [corrected paired trial](levels/level-04.md#waveform-padding-repair-and-model-retirement--2026-10-22) keeps Turbo for the interactive M2 prototype and retires the verified full-v3 cache under explicit owner authorization. Full-v3 remains the better Gujarati result on this small corpus; its extra decoding cost and weaker Hindi do not justify replacing the interactive baseline.

The owner resumed paid Claude and supervised microphone checks, with [recorded outcomes](levels/level-06.md#supervised-claude-and-booking-follow-up--2026-10-22). Natural, explicit consent after a selected readback is deterministic and no longer requires another extraction request; unknown/tentative/negative/revised responses retain their existing guards. A real TEST ONLY booking and SQL restore passed. This does not waive representative/native scoring or declare a latency SLO, and does not authorize section 21 or automatic paid testing in future tasks.


## 2026-10-23 — Section 21 logging contract

Extend stdlib logging rather than add another framework. Emit UTC JSON with reviewed events/metadata and task-local context; generate request/correlation IDs server-side, never from caller identity or supplied headers. Keep one prototype correlation across attempts, distinct attempt/request IDs and code-owned stage/language. Reuse the existing voice error enum. Preserve stdout reports, default PII-free durable receipts and independent third-party sinks; omit legacy free-form message/traceback content at the JSON boundary. Existing redaction remains defense in depth. No DB/schema/provider/model or dialogue change; no paid/microphone activity or new gate approval. [Level 6](levels/level-06.md#section-21--structured-logging) owns evidence and limits.


## 2026-10-23 — Sections 22–23 bounded streaming and cancellation

Add an opt-in streaming path to the existing local composition rather than replace the carrier pipeline. Reuse the deterministic controller, safety/audit, booking authority and phrase cache. Use bounded per-stage queues with explicit drop/backpressure policy, isolated bounded CPU processes and first-software-audio timing. Keep unstable partials out of business state and do not invent incremental Whisper output. Preserve physical half duplex and provider/model settings. One task per response keeps the final-transcript consumer alive after interruption. Disconnect/clear failures still attempt close/release/durable finish; record disconnect via existing JSON events and the existing terminal `failed` call status. Advanced epochs wait for their core gate. No paid/microphone run, download, working `roma` change, migration or native-quality gate approval. [Level 7](levels/level-07.md#sections-2223-implementation) owns exact defaults and evidence. The owner renewed commit-and-push after every completed increment; earlier local-only phase delivery is historical.

## 2026-10-23 — Section 24 supervised Redis/SQL milestones

Use awaited short checkpoint writes instead of detached background work for the teaching implementation. Serialize a single frozen pending snapshot, retry known transient SQL failures once, publish Redis only after SQL commit, and prefer SQL on restore. A non-refreshing 360-second owner-token lease surrounds the five-minute call ceiling; accept bounded crash-admission delay until expiry. Preserve strict ownership and SQL booking authority; allow conversational offers from fresh SQL when advisory holds fail. Keep phrase/state/counter lifetimes explicit and numeric checkpoint metrics PII-safe.

Accepted teaching recovery point: acknowledged SQL milestones survive Redis loss; in-progress work after the last milestone may be lost. Redis is reconstructible transient state, so no running persistence setting changes are necessary. Production deployment RPO/RTO, advanced Streams/replay and native/reliability acceptance remain separate owner decisions after the gate. Provider/transport settings, working `roma`, migrations and models are unchanged.

## Section 25 — Inherited provider budgets and safe retries

Keep existing Claude settings and approved fallback. Share its configured turn deadline across extraction and wording, including admission/backoff; isolate timers to individual provider awaits. Retry only known transient failures before any output and within budget. Add a local adapter circuit, not distributed infrastructure. Retain native work ownership and telephony zero-retry semantics. Preserve Sarvam streaming and model/provider choices. User acceptance activities and advanced work wait until sections 25–28 are complete and separately reviewed.

## Section 26 — Reuse durable jobs and make optional effects explicit

Keep Dramatiq/Redis UUID delivery with PostgreSQL business ownership and one recording consumer. Preserve default metadata summaries; explicit paid opt-in selects a separate capped Claude instance on a lower-priority queue. Followup processing requires consent plus an injected provider-idempotent sender; no sender/CRM/contact is configured automatically. Record exhausted processing reasons/attempts instead of silently losing intent. Ordinary Messages is implemented; Message Batches/discount is only a documented future option. Live/paid/native/advanced acceptance remains deferred.
