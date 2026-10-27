# Observability, testing and delivery

**Present:** [Section 21 JSON logging](levels/level-06.md#section-21--structured-logging), sections 45–46 bounded telemetry/optional spans and normalized SQL usage/cost projections, offline evaluations and database/worker integration tests. **Pending:** final combined-source verification, actual production dashboards/reconciliation, sections 47–48, real local-model capacity/chaos reports and full deployment packaging.

## Measurement design

Start with a question: which call/turn/stage failed, where time was spent, what retried, what effect committed and what resource saturated? Use PII-safe correlation and stable event names. Do not use phone numbers/transcripts/tokens as trace or metric labels.

The shared logger serializes approved metadata only; arbitrary messages and tracebacks become `legacy_log` without free-form content. Prototype stdout reports remain CLI output, while operational events use JSON on stderr. The [runbook](runbook.md#structured-logging-section-21) explains how to follow one call.

Measure endpoint/ASR/extraction/LLM first token/TTS first audio, conditional retrieval/fallback, end-to-end response, queues, event-loop lag, database pools, Redis, GPU, safety, job attempts and normalized carrier/hardware costs. SQL outcomes must reconcile with durable records.

L11 adds OpenTelemetry/Prometheus/Grafana; tools consume defined signals rather than inventing them. Optional supervisor PubSub/WS is best effort and requires role/security controls. A dashboard with sample data is not production evidence.

`model_registry`, `benchmark_runs` and `benchmark_results` now provide the relational evidence foundation. Future runners must pin model/version and dataset identity, record hardware/configuration and meaningful units/sample counts, and keep PII/sample media out of these metadata rows. A schema row or `is_active` flag is not a license approval, completed benchmark or production selection. See the [schema catalog](20-relational-schema.md).

Sections 22–23 add PII-safe queue snapshots, producer/consumer wait, drop/discard counters and synthetic concurrent first-audio/heartbeat evidence. Streaming stage timings overlap; a fake software write is not acoustic playback, and the four-call report cannot establish native capacity or an SLO. [Level 7](levels/level-07.md#sections-2223-implementation) owns these limits and cancellation/pressure policy.

## Tests and capacity

Unit/provider-fake/API/repository/worker/concurrency/evaluation tests make no paid calls. PostgreSQL tests need a disposable database; report skips and environment limits. Manual local-model/GPU and authorized carrier checks are separate evidence.

L12 exercises slow providers, Redis loss, queue pressure, abrupt disconnect and RAG fallback. Separate mocked backend load from real GPU capacity. Grow 1/10/25/50/100 sessions only while agreed latency/error/resource gates hold; stop at measured saturation. Record P50/P95/P99, sample/configuration/hardware identity, queue wait, cold/warm state and failures.

## Delivery and SLOs

L1 introduces the expanded quality/CI skeleton; L12 adds security scans, migration/regression checks and zero-external-GenAI enforcement for the final local profile. L13 introduces full Docker/GPU Compose packaging, mounted weights/data, runtime secrets, readiness, clean-host startup, backup/restore and rollback.

Existing database/Redis Compose predates v4 and does not complete L13. Native-service startup remains the foundation-learning path. Roadmap latency/event-loop/packet-loss numbers are provisional and transport-specific. Agree SLO/error-budget policy after measurements; recurring misses prioritize reliability.

See [verification](12-verification.md), [L11](levels/level-11.md), [L12](levels/level-12.md), [L13](levels/level-13.md) and [runbook](runbook.md).

## Section 24 checkpoint signals

Reviewed JSON events add `checkpoint_completed`, `checkpoint_failed` and `state_cache_failed`. Failure events use ERROR. The finite nonnegative metric allowlist is checkpoints, failures, retries, redis_failures, recoveries, pending and lag_ms; transcripts, slot values, Redis keys/tokens, database URLs and arbitrary extras are omitted. Completed checkpoint latency includes its awaited milestone work; no numeric SLO is asserted. Existing queue and turn metrics remain separate. [Level 7](levels/level-07.md#section-24-live-state-and-supervised-checkpoints) defines the supervised retry and recovery contract.

## Section 45 bounded telemetry

`roma/core/telemetry.py` exposes cumulative Prometheus histogram/counter/gauge text with reviewed stage/outcome/route/queue labels and no retained samples or caller payload. Optional OpenTelemetry uses a private provider with fixed attributes and no automatic exception recording/exporter. Live Pipecat probes measure VAD-stop confirmation through software playback completion; endpointing uses the reported silence window. These are software boundaries, not first-audible/physical stop latency. Process CPU and peak RSS are available; unknown GPU/current RSS observations stay absent rather than zero. `/metrics` access and proxy caveats are in [security](07-security.md#sections-4346-service-and-observation-privacy).

## Section 46 normalized usage and costs

`UsageEvent`/`CostService`/`CostPostgresRepository` separate ordinary input, cache reads/writes, output tokens, carrier/audio/GPU seconds, characters and cache hits. One transaction writes an immutable idempotency receipt and exact Decimal cost lines. Changed same-key content conflicts; missing usage/rates/billing basis/cache-write TTL remains unpriced. Metadata retains raw units, exact amounts and price provenance alongside compatibility numeric columns. UTC day/month/provider/call/turn projections retain separate currencies, unknown-unit counts and completeness flags; no missing GPU time or local costs are invented. Historical rows are not repriced. The INR safety reservation ledger remains independent.

The [official Haiku 5.5 overview](https://platform.claude.com/docs/en/models/haiku-5-5/overview), verified 2026-10-26, supplies the versioned direct-standard USD rates per million tokens:

| Prompt total | Input | Output | 5-minute write | 1-hour write | Cache read |
|---|---:|---:|---:|---:|---:|
| Up to 100,000 | 0.10 | 0.50 | 0.125 | 0.20 | 0.01 |
| Above 100,000 | 0.50 | 2.50 | 0.625 | 1.00 | 0.05 |

The threshold includes ordinary input plus cache reads/writes. Only the exact reviewed model/direct billing basis is priced; discounts, other regions/providers and exchange rates are not assumed. A matching provider-observed amount/currency can be retained separately. These are estimates with provenance, not invoices or historical repricing. [Level 11](levels/level-11.md) retains the native dashboard/reconciliation gate and unstarted sections 47–48.

The [native observability lab](../configs/observability/README.md) configures a loopback Prometheus scrape and provisioned Grafana **Roma measured operations** dashboard. One API worker matches one process-local registry; multi-worker aggregation and separate worker-process scraping require explicit targets. Only observed samples appear. Grafana disables anonymous access and usage/update reporting, and its first Admin password must be provided privately. Existing native binaries and an authorized operator run are required; checked-in JSON/YAML alone is not readiness evidence. Tracing remains disabled by default. With the explicitly provisioned `observability` extra and `TRACING_ENABLED=true`, app lifespan uses a private SDK and a 256-entry in-memory reviewed metadata processor, cleared on shutdown; it restores the prior tracer and configures no global provider or network exporter.

## Section 52 dependency, secret and target-policy gates

The explicit non-secret [production target](../configs/production.local.env) selects Claude Haiku 5.5/low effort, loopback authenticated local HTTP Whisper Turbo MLX and Indic-TTS, and pinned local multilingual embeddings. `scripts/check_production_policy.py` checks the finite literal schema without loading private `.env`, printing input values, starting models or changing the preserved `legacy_cloud` factory default. RAG remains disabled until its separate acceptance. Profile validation proves configuration intent; credentials, manifest/hash verification, RAM, native audio quality and operational rollout remain separate.

[Official Gitleaks 8.30.1](https://github.com/gitleaks/gitleaks/releases/tag/v8.30.1) is installed explicitly with reviewed Darwin-arm64/Linux-x64 archive SHA-256 checksums from the official release. The installer rejects mismatched, missing and symlink binary members. The scanner runs full Git history and a private snapshot of every current Git-visible tracked/untracked file, with redacted findings containing only paths/rules/line/commit identifiers. It never reads ignored private `.env` or runtime files. Default rules remain enabled; [reviewed exceptions](../configs/security/gitleaks.toml) require both the particular rule/path and exact synthetic content or source-hash-map syntax. No whole-test-directory, whole-file or history baseline exclusions exist. A real scanner regression detects a removed historical credential and a fresh untracked credential in a legacy-exception test path despite an inline `gitleaks:allow` comment. The final local full-history/worktree scan found zero outstanding findings.

The initial actual `pip-audit==2.10.1` scan returned 103 advisory rows across nine packages, including duplicate advisory IDs. Minimal patched floors are now constrained in `pyproject.toml` and resolved in `uv.lock`:

| Dependency | Patched locked release |
|---|---|
| aiohttp | 3.14.4 |
| certifi | 2026.10.19 |
| cryptography | 50.0.0 |
| filelock | 4.2.1 |
| multidict | 6.9.1 |
| nltk | 3.10.4 |
| Pillow | 13.0.0 |
| PyJWT | 2.15.0 |
| virtualenv | 21.14.10 |

Pipecat 1.6–1.13 require Pillow below 13, so the telephony extra explicitly pins Pipecat **1.14.0**, the first checked compatible release. Its upstream tokenizer changed to sentencex; Roma retains an explicit patched NLTK dependency for its own existing tokenizer-data contract. No speech model, provider selection or adapter is changed by these dependency declarations. Cryptography's previous major-version ceiling had to move to permit the patched release. No advisory was ignored.

Final real audits of the hashed locked base/backend/telephony/workers/observability/embeddings/voice-lab/MLX/duplex/dev/security/load closure returned **zero known vulnerabilities**: 178 packages on macOS arm64/Python 3.12, and 191 for Linux x86_64/Python 3.12 after evaluating the same export's platform markers. The audit did not install/download models. This is a dated advisory result, not a promise about future disclosures, independently provisioned Indic-TTS environments or model artifacts. The audited lock SHA-256 is `5cd98e26cdcd32a7c004fb7238cbee8a965062959b7db21872ffaf60c4cb8310`.

A fresh isolated locked runtime left the working voice environment untouched. Focused security, JWT, field encryption, PII and tokenizer checks passed **33 tests**, with existing audioop/AudioContextTTSService deprecation warnings; the three security scripts passed mypy and Ruff. The complete final catalog owns broader Pipecat compatibility evidence; neither these focused checks nor dependency auditing pass native/physical acceptance.

CI preserves native PostgreSQL/pgvector/Redis setup and enforces lint, typing, the complete offline unit/integration catalog, a separate real secret/dependency audit job, target policy validation, and API/worker/model-contract container builds with nonroot image identity. The mock golden RAG mechanical regression runs on every CI change, including material retrieval changes, without paid APIs or model downloads. It cannot establish real Claude groundedness or native retrieval quality.

## Sections 49–51 reproducible evaluation, failure drills and mock load

The required acceptance inventory lives in `configs/evaluation/mandatory-matrix.json`: 35 checks, explicit evidence kinds/minimum counts/metric names and unset or agreed targets. `project_readiness` keeps absent measurements null, refuses non-finite values and passes a row only with eligible independently approved evidence and all targets met. Synthetic/mock receipts cannot satisfy native metrics; the 100-request SQL race requires exactly one winner/99 conflicts/100 attempts. The automatic runner never grants approval, and `--require-full-gate` fails while mandatory evidence is pending.

`scripts/evaluate_project.py --run-offline` executes 70 public scripted mock turns plus all 124 controller fixture-replay turns and safety canaries. `--run-chaos` invokes the exact suites in `configs/evaluation/chaos-regressions.json`, including parameterized outcomes; `--allow-disposable-services` additionally selects real temporary SQL/Redis recovery and booking tests. Docker access is disabled for this subprocess so shared SQL fixtures fall back to native binaries without image pulls. Reports retain JUnit/output/test/source-tree/configuration fingerprints and metadata-only outcomes. Missing selections, skips, collection errors, failures and timeout are visible and cannot become a complete pass. Timed-out pytest receives an interrupt before hard termination so fixture teardown can run.

The fresh report contains 36 passed failure cases: real Redis key deletion/expiry and SQL restore, synthetic cache/durable-write/provider faults, delayed STT/LLM/TTS, output pressure, ASR/generation/synthesis/playback disconnect, EOF draining, delayed RAG recovery, Claude 429/5xx/retry-after/budget behavior and 100 competing HTTP SQL bookings. The expanded ASR disconnect regression first failed: capture had stopped without cancelling pending recognition. Terminal disconnect now cancels recognition/final workers after common call cleanup, while normal EOF drains. This is fake-AI/transport plus disposable-backend evidence, separate from Redis server crash drills, real provider outages, physical latency and native quality.

`scripts/load_backend.py` runs a fixed closed-loop 1/10/25/50/100 ladder of full 12-turn fixture-driven text calls through actual ASGI HTTP, controller/extraction parsing and final safety. Optional owned native SQL `SELECT`/connection probes and Redis `PING` are not per-turn checkpoints/booking writes. Every attempted request, including timeout/error, enters latency/error accounting; unavailable request quality remains null. A measured quality failure or crossed configured error/fallback/P95/loop-stall/RSS threshold stops later tiers. Supplied limits are provisional; unset limits remain `policy_status=unset`. Native GPU/VRAM/TTFT, RAG and unmeasured server resources stay null. First request/process-reuse conditions and source/dependency/model identities accompany the results.

The dated 372-call/4,464-turn smoke and its complete percentiles/resources are in [Level 12](levels/level-12.md#mock-backend-capacity-experiment) and the [load receipt](../benchmarks/evaluation/mock-backend-native-20261027.json). They establish mock-backend behavior at the tested finite load only. Paid/local real-model activity is not automated; explicit real-mode flags lead to supervised acceptance with an approved spend budget, quality/latency stops and account/hardware limits. Optional Locust is restricted to the owned loopback mock lab. The [readiness report](../benchmarks/evaluation/project-readiness-20261027.json) retains nine unapproved receipts and a false full gate; no production SLO or native acceptance follows from the offline results.

### Section 52 Pipecat compatibility evidence

The security upgrade exposed two upstream compatibility seams. Roma now owns and clears the adaptive VAD-stop timestamp at speech/turn boundaries because Pipecat removed the former private timestamp; the existing remaining-deadline behavior is preserved. Twilio envelopes require a known event before native deserialization or sequence updates, preserving malformed-control rejection when upstream now silently returns no frame. Existing strategy/controller test fixtures use `FrameProcessorSetup`; their behavioral assertions remain intact. All **582 realtime, carrier-validation and CI-architecture tests passed** in the patched isolated runtime, with 26 upstream deprecation warnings. The standalone lightweight security-CI environment separately passed its eight tests without the unrelated global tokenizer fixture. The sanitized [section 52 receipt](../benchmarks/security/section52_audit.json) retains scanner/lock/source identities and audit counts; complete-catalog, Docker and native acceptance evidence remain separately scoped.


Final section52 review also proved that ordinary Git history scanning omitted merge-only additions. History scanning now includes first-parent merge diffs across all refs; a real disposable merge-only secret regression failed before the fix and passes after it. Shallow clones are rejected rather than described as complete scans. Existing narrow content/path false-positive rules remain; no additional exclusion was needed. Section53 startup now refuses partial/lost key material when local initialization or retained project volumes exist; keys are restored rather than silently replaced over encrypted data. Real repeated mock-stack starts preserved the original key bytes. These cases have their own recorded security/deployment receipts.
