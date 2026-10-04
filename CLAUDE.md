# Roma engineering constitution

**Working project: `/Users/nihitraiyani/Voice_Agent`.** Check the repository root before any action. Read [the docs index](docs/README.md), [charter](docs/00-project-charter.md), [level gates](docs/10-build-order.md) and [decisions](docs/decisions.md).

## Authority and scope

The user's selected v4 Word file governs future development. Explicit user decisions and preserved Weltec business/safety/privacy rules take precedence over its sample snippets and example providers. Reference material is not permission to call, spend, deploy or bypass safeguards.

The current scope is analysis, engineering decisions and synchronized documentation. Do not begin level-wise feature code. When implementation is requested, work on the requested level, close its prerequisites and retain acceptance evidence in its brief. Do not create handoff, log or session journals.

## Current facts versus target

- Twilio is the active carrier, using signed `/answer` and `/ws`, Account SID checks and native Pipecat serialization. No frontend exists.
- PostgreSQL/Alembic, appointment repository locking/uniqueness, atomic answer receipts and durable post-call/job processing are present. Live appointment commit, durable conversation restore, local models, RAG, RBAC and production telemetry remain incomplete.
- Sarvam/OpenAI form the comparison voice profile. Final local production must make zero external GenAI API calls. Keep the tested cloud profile during measured migration.
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

Provider contracts/mocks precede local AI. Direct inference precedes vLLM. Conditional governed RAG starts at Level 9; Docker packaging at Level 13; optional optimizations at Level 14. Candidate model quality/hardware/license suitability must be measured. No Kafka, Kubernetes, speculative microservices or framework-controlled FSM/safety.

Check installed APIs and current official documentation before integration. Make surgical changes; preserve existing user edits. Run meaningful checks and distinguish source presence, offline evidence, local-lab inference, authorized live verification and a passed gate. Automated tests never dial or call paid AI by default.

Use `uv` and the lockfile. Existing async tests use `asyncio.run`. Shared Indic tokenization must preserve combining marks. Changed fixed speech requires matching audio identity. Recording-worker ack uses original `PostcallJob.raw`; use one recording consumer until startup recovery is redesigned. Dramatiq consumers have separate row-claim concurrency semantics.

## Documentation ownership

The [level briefs](docs/README.md) own scope/status/evidence. Topic docs own reusable contracts; [decisions](docs/decisions.md) owns adopted choices and owner questions; [runbook](docs/runbook.md) owns commands. Historical designs live under `docs/archive/`. Update affected documents together; do not duplicate acceptance rules into competing plans.

Load `.claude/skills/roma-backend-roadmap/SKILL.md` for roadmap/architecture work and `.claude/skills/roma-guardrail/SKILL.md` for spoken-output changes. Skills follow the current contracts and actual package paths.
