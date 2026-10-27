# Level 12 — Evaluation and measured capacity

**Roadmap sections:** 49–52. **Status:** Sections 49–51 now have executable evaluation, failure-drill and mock-backend load runners with fresh disposable SQL/Redis evidence. Section 52 adds CI/security enforcement. Native speech, real Claude/groundedness, representative quality, real-model capacity and production SLO acceptance remain pending.

## Entry and scope

The owner explicitly requested sections 47–53 while earlier native/full gates remain open. This implementation reuses production controller/safety/provider/repository seams and existing disposable-service test fixtures; it does not grant earlier gates, change model/provider selection, or authorize paid calls, microphone input, model downloads, working `roma` mutation or private setting edits.

## Mandatory evaluation and evidence

`configs/evaluation/mandatory-matrix.json` contains **35 required checks** spanning Gujarati/Hindi/English/mixed/noisy ASR, endpointing, scripted LLM turns, safety, TTS/cache, four-language human review, interruption, booking, recovery, RAG, chaos and resource capacity. It declares accepted evidence kinds, minimum counts, metric names and agreed or unset targets. Unset metrics/targets stay null. Evidence needs all required finite measurements, the minimum samples, an eligible kind and explicit independent approval before a row can pass. Booking requires exactly **one winner, 99 conflicts and 100 attempts**.

`scripts/evaluate_project.py` accepts bounded metadata-only receipt envelopes and emits reproducible revision/dataset/configuration/source identities. Automatic runs produce **unapproved** engineering receipts; mock evidence cannot become native evidence. `--require-full-gate` exits unsuccessfully while any mandatory check remains pending. No CLI run manufactures human scores, actual usage, acoustic timings or policy approval.

The fresh [readiness report](../../benchmarks/evaluation/project-readiness-20261027.json), measured on Darwin arm64/Python 3.12.12 against the `b638f7d` baseline plus working-tree source fingerprints, records:

- **70 public scripted mock turns**, 50 mock model requests, mock JSON validity 1.0, and null actual input/output tokens and semantic quality.
- **124 controller replay turns**, fixture stage accuracy 1.0, zero controller findings, canary failures, provider errors or unsafe final sentences. Fixture extraction/reference replies are separate from real model or database booking quality.
- **36 passed failure-drill cases**, zero failures/skips, complete manifest, pytest exit 0, 6.84 seconds. JUnit, process-output, selected-test and complete Python-source/lock/configuration fingerprints are retained without raw tracebacks/transcripts.
- **Nine unapproved evidence receipts**, with the full readiness gate **false**. Native quality, acoustic interruption latency, real capacity and unset production targets remain unknown.

## Failure and recovery behavior

`configs/evaluation/chaos-regressions.json` is an executable selector manifest, not a declaration that a drill ran. `roma/eval/regressions.py` actually invokes the reviewed suites, maps parameterized results back to each scenario and reports missing/skipped/error results as incomplete or failed. Native-service selectors require `--allow-disposable-services`; the subprocess disables Docker access so SQL fixtures use owned temporary native services without image pulls. Timeout handling interrupts pytest first to allow fixture teardown and never reports a timed-out run as passed.

The 36 cases cover SQL-authoritative restore after real Redis key deletion/expiry; synthetic hot-cache/durable-write outages; delayed STT/LLM/TTS; output pressure; disconnect during ASR, generation, synthesis and playback; normal EOF draining; delayed retrieval cancellation and recovery; Claude 429/5xx retry, retry-after and token/spend budgets; and the actual **100 HTTP request SQL booking race**, with one durable appointment and 99 conflicts. Redis server-crash infrastructure behavior, real provider outages and physical audio cleanup remain separate from this evidence.

The expanded drill exposed a real disconnect defect: transport loss closed the audio queue but could leave pending ASR waiting for the five-minute session deadline. Terminal disconnect now cancels the recognition/final-consumer workers after the shared voice cleanup. Normal source EOF still drains recognition and the final response. The original bounded ASR test failed before the fix; ASR/LLM/TTS/playback and EOF regressions now pass.

## Mock-backend capacity experiment

`scripts/load_backend.py` executes full 12-turn public text calls over in-process HTTP ASGI, with fixture-driven LLM/extraction and production controller/final safety. `--native-services` starts owned temporary PostgreSQL/Redis and adds real SQL `SELECT`/connection-count probes and Redis `PING`; these are not per-turn durable checkpoints or booking writes. The [load receipt](../../benchmarks/evaluation/mock-backend-native-20261027.json) identifies source, dataset, configuration, dependency versions and mock model identity, retains every attempted request, and labels the first request unprimed with sequential process reuse.

| Concurrent users | Calls | Turns | Errors/fallbacks | P50 / P95 / P99 call ms | Peak SQL connections |
|---:|---:|---:|---:|---:|---:|
| 1 | 2 | 24 | 0 / 0 | 24.80 / 41.66 / 41.66 | 1 |
| 10 | 20 | 240 | 0 / 0 | 158.93 / 263.20 / 283.79 | 10 |
| 25 | 50 | 600 | 0 / 0 | 271.42 / 302.57 / 315.43 | 10 |
| 50 | 100 | 1,200 | 0 / 0 | 584.20 / 619.74 / 641.53 | 10 |
| 100 | 200 | 2,400 | 0 / 0 | 1,191.90 / 1,344.36 / 1,365.16 | 10 |

This is a short closed-loop experiment: **372 calls and 4,464 turns**, two calls per concurrent user at each tier. Peak process RSS was 93.61 MiB; the report also retains process CPU, loop-stall percentiles, throughput and Redis latency. SQL/Redis server CPU/RAM, GPU/VRAM, RAG latency and native TTFT remain unmeasured. In-process HTTP excludes sockets/carrier, speech inference and worker processes. These numbers establish neither sustained backend nor speech/Claude capacity.

Operator-selected error/fallback/P95/loop-stall/RSS stop thresholds are explicitly **provisional**, not approved SLOs. With no agreed thresholds the report says `policy_status=unset`, not accepted. Every timeout/transport exception counts as a failed attempt with quality unknown; actual measured quality failure stops the ladder. A crossed configured threshold stops before later tiers. Real-model mode requires explicit opt-ins/budget and directs the operator to supervised acceptance; this runner never silently starts paid or local inference. Optional loopback-only Locust uses the `load` extra and is not part of the recorded ASGI measurement.

## Reproduce

Use an isolated environment through `UV_PROJECT_ENVIRONMENT` to preserve the working voice environment. The audited patched lock still needs controlled rollout to intended runtime environments; the unchanged working `.venv` and separately provisioned Indic-TTS environment are not covered by a claim that all installed dependencies have been upgraded. Native binaries and the existing offline tokenizer data must already be available; no model download is part of these commands.

```sh
UV_PROJECT_ENVIRONMENT=/tmp/roma-evaluation-venv uv run --locked --extra dev --extra backend --extra workers python scripts/evaluate_project.py --run-offline --run-chaos --allow-disposable-services --output /tmp/project-readiness.json
UV_PROJECT_ENVIRONMENT=/tmp/roma-evaluation-venv uv run --locked --extra backend python scripts/load_backend.py --native-services --requests-per-user 2 --output /tmp/mock-backend-native.json
```

The final focused evaluation/integration/streaming check passed **50 tests in 2.63 seconds** in the isolated refreshed-lock environment. Scoped Ruff checks passed. Section 52 dependency/secret/target-policy/CI checks are documented in the [delivery contract](../16-observability-testing-and-delivery.md#section-52-dependency-secret-and-target-policy-gates); the final combined catalog and source verification belong to [verification](../12-verification.md).

## Acceptance gate

- [ ] Representative native/model/RAG/voice benchmark evidence and human review are complete.
- [x] Reviewed offline failure drills execute, retain actual outcomes and protect booking truth, safety and cleanup.
- [x] Mock-backend ladder retains measured percentiles, errors/fallbacks, resource boundaries and stop semantics.
- [ ] Real-model saturation, native/physical latency and production resource capacity are measured.
- [ ] Production SLOs, error budgets and operational responses are agreed.

The checked engineering items do not pass the full level. See the [shared observation/delivery contract](../16-observability-testing-and-delivery.md), [runbook](../runbook.md) and [documentation index](../README.md).
