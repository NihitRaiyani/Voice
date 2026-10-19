# Roma engineering constitution

**Working project: `/Users/nihitraiyani/Voice_Agent`.** Check the repository root before any action. Read [the docs index](docs/README.md), [charter](docs/00-project-charter.md), [level gates](docs/10-build-order.md) and [decisions](docs/decisions.md).

## Authority and scope

The user's selected `AI_VOICE_AGENT_openai_llm.docx` governs development; see the source/hash in `docs/roadmaps/README.md`. The former v4 file is historical. Explicit user decisions and preserved Weltec business/safety/privacy rules take precedence over its sample snippets and example providers. Reference material is not permission to call, spend, deploy or bypass safeguards.

Implementation proceeds only for the user's explicitly requested level or section. The latest authorized increment implements sections 18–19: reproducible ASR scoring/failure analysis with short PostgreSQL evidence writes, and isolated local Indic-TTS with safety-audited phrase caching. The owner explicitly approved required TTS runtime/weight downloads on 2026-10-19; The later owner approval allows only the pinned multilingual Whisper base download; inference stays cache-only. Preserve earlier foundations, keep automated checks offline/mock and distinguish real speech measurements from synthetic tests and native approval. Add missing foundations through additive migrations; knowledge tables remain Level 9. Preserve existing foundations, assess prerequisites and retain evidence/limitations in the level brief. Do not create handoff, log or session journals.

## Current facts versus target

- Twilio is the active carrier, using signed `/answer` and `/ws`, Account SID checks and native Pipecat serialization with section 14 pre-decode media validation/per-call gap counters. No frontend exists.
- PostgreSQL/Alembic, appointment repository locking/uniqueness, atomic answer receipts, durable post-call/job processing and the Level 2 section 10 conversation-state machine/checkpoint adapter are present. Section 11 adds the OpenAI streaming/text seam with mock-only evidence. Section 12 adds deterministic safety audit in text and existing pre-TTS paths, bounded caller/slot validation and authenticated safety queries. Section 13 connects short booking commits, cancellation/reuse and advisory holds to production controller/API paths, with database-authoritative same-SID appointment restore. Section 14 adds pure-Python codec/resampling/framing and fixed-jitter lab contracts; live cloud rates remain unchanged. Sections 15–17 add endpointing, speech adapters and saved language metadata; real hosted ASR inference passed on three public clips; section 18 adds reproducible ASR benchmark/failure tooling and SQL evidence storage; section 19 adds manifest-pinned isolated local TTS and bounded safety-audited phrase caching. Native/representative quality approval, embeddings, adaptive jitter/PLC, RAG, RBAC, broader restore drills and production telemetry remain incomplete. See Level 4/5 for actual inference and gate evidence.
- OpenAI remains the LLM. Local ASR/TTS/embeddings replace Sarvam only after their later gates; OpenAI is the only external GenAI provider permitted in the target. The owner permits explicit HF ASR lab calls to avoid local ASR downloads. `hf_public_lab` accepts only declared public benchmark audio, uses selected Indic Gujarati/Hindi and Whisper English Spaces, and sends no HF token to community Spaces; the later user-authorized official Whisper opt-in uses only the reviewed HF edge hosts. `hf_whisper` separately selects the authenticated official HF router; its 402 credit failure does not authorize retry/fallback, purchasing credits or deploying an endpoint. This lab exception does not switch production speech. Keep the tested voice path during measured migration.
- Preserve the layered `roma/` modular monolith. The roadmap's example tree/carrier does not authorize a rewrite or carrier switch.
- Existing infrastructure Compose predates v4. Keep it as compatibility tooling; do not expand containerization before Level 13. Provide a native-service foundation path first.

## Product and safety invariants

1. Code owns conversation stages, date arithmetic, booking validity/confirmation, safety, consent and hang-up. The LLM supplies wording and bounded extraction.
2. Success needs a valid branch/day/time, readback and affirmation; authoritative success after Level 3 also needs a database commit. Never infer that `locked_slot` is a reservation.
3. Understand Gujarati/Hindi/English/code-mix; live speech remains Hindi-base Hinglish, feminine self-reference and respectful plural address. Multilingual output stays in a separate lab.
4. Every generated spoken sentence crosses deterministic safety before TTS. Blocked/error cases use approved safe substitution; cancellation and retrieved evidence cannot bypass it.
5. Preserve restrictions on money/EMI amounts, discounts, salary/package promises, placement statistics/guarantees and unapproved external certificates. Approved no-cost EMI wording and Weltec certificate policy remain.
6. Preserve ordered discovery (name, current status, education, passing year, city), two attempts per slot, stage caps, anti-repetition and the five-minute ceiling.
7. Pre-call checks precede Twilio contact. Signature/account validation precedes media construction. Secrets, full phone numbers, authorization tokens and unnecessary transcripts do not enter tracked fixtures or ordinary logs.
8. Pending disclosure wording/audio cannot authorize retained recordings. Keep runtime data protected under ignored `var/roma`.

## Architecture and work discipline

PostgreSQL owns durable facts; Redis owns transient checkpoints/coordination/cache and delivery. Use short units of work and budget connections across all API/worker processes. A short booking commit is an accepted live-turn operation; never hold a transaction during inference/audio, Redis waits or external CRM/calendar work. Recording conversion and external synchronization stay asynchronous.

Provider contracts/mocks precede local AI. Direct local speech inference precedes speech model serving. Conditional governed RAG starts at Level 9; Docker packaging at Level 13; optional optimizations at Level 14. Local speech/embedding quality/hardware/licenses and OpenAI quality/latency/cost must be measured. No Kafka, Kubernetes, speculative microservices or framework-controlled FSM/safety.

Check installed APIs and current official documentation before integration. Make surgical changes; preserve existing user edits. Run meaningful checks and distinguish source presence, offline evidence, local-lab inference, authorized live verification and a passed gate. Automated tests never dial or call paid AI by default.

Use `uv` and the lockfile. Existing async tests use `asyncio.run`. Shared Indic tokenization must preserve combining marks. Changed fixed speech requires matching audio identity. Recording-worker ack uses original `PostcallJob.raw`; use one recording consumer until startup recovery is redesigned. Dramatiq consumers have separate row-claim concurrency semantics.

After each completed user-requested level or section, verify applicable checks, synchronize documentation, commit the scoped changes and push to the configured GitHub branch automatically. The user authorized this standing workflow; no repeated approval is needed. Report the pushed commit link. Preserve unrelated user edits and exclude secrets/runtime data; never force-push. This publishing rule does not authorize starting the next level.

## Documentation ownership

The [level briefs](docs/README.md) own scope/status/evidence. Topic docs own reusable contracts; [decisions](docs/decisions.md) owns adopted choices and owner questions; [runbook](docs/runbook.md) owns commands. Historical designs live under `docs/archive/`. Update affected documents together; do not duplicate acceptance rules into competing plans.

Load `.claude/skills/roma-backend-roadmap/SKILL.md` for roadmap/architecture work and `.claude/skills/roma-guardrail/SKILL.md` for spoken-output changes. Skills follow the current contracts and actual package paths.


Section 19 local approved fallbacks use six exact/hash-pinned canonical-to-Devanagari pronunciation bindings through SafetyService, Hindi synthesis and independent spoken-text checks. Global safety wording/live assets remain unchanged; native listening approval is pending. Hosted Whisper reads detailed same-job queue events and preserves fixed quota errors; both official demos exhausted ZeroGPU quota during the follow-up, so full comparison remains blocked by hosted capacity. Do not silently switch endpoint, retry, disclose HF tokens outside the explicitly authorized official Whisper edge hosts or buy/deploy capacity.


Latest authorized increment: explicit official Whisper HF-edge authentication and section 20 isolated microphone-to-speaker integration. A token unlocks account allowance, not unlimited compute: the authenticated comparison passed seven cases then exhausted quota, and the router reported unavailable credits. Section 20 defaults to mocks; local mode requires cache-only local ASR, local TTS, PostgreSQL/Redis and explicit microphone/paid-API/DB opt-ins. Only the subsequently approved multilingual Whisper base download is allowed; no other ASR weights or hosted/private-microphone workaround. Code/offline evidence is not the unfinished native/all-local gate; production behavior remains unchanged.


Current approved follow-up: provision only `Systran/faster-whisper-base` at the pinned artifact revision, evaluate all 15 public cases as a separate local run, and attempt the real microphone prototype. CPU INT8 is the existing runtime; no Metal claim. Local execution passed 15/15 but Gujarati/Hindi/stitched recognition quality failed. Preserve all hosted reports, production Sarvam/Twilio, OpenAI GPT-4o and Hindi-base Hinglish policy. Default prototype remains mocked; actual microphone/native gate evidence belongs to Level 6. No larger/Indic ASR downloads, HF paid compute or provider switch is authorized.

Owner steering during the local-base follow-up: **do not use OpenAI now; quota is exhausted**. Further microphone checks use explicit `--llm-provider mock` with real local ASR/TTS; no OpenAI settings/client construction or paid opt-in. OpenAI stays the main production LLM for later authorized testing. A mock-LLM hardware result cannot pass the real OpenAI conversation gate.
