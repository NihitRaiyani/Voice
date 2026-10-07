# Level 2 — Text conversation and local LLM

**v4 sections:** 10–12. **Status:** Sections 10–11 state machine/checkpoint adapter, direct Transformers and optional Apple INT4 providers, text console and language lab implemented. Selected 1.7B native-quality/voice-latency approval, suitable-host 8B comparison and section 12 structured safety audit remain pending.

## Entry gate

Level 1 foundation and provider contracts.

## Scope when implementation is requested

Run the deterministic seven-stage conversation as a text-only application. Persist and restore phase/slots/milestones with schema/session identity. Keep extraction, relative time resolution and confirmation authority in code.

Implement direct Qwen3-8B Transformers inference as a candidate provider; measure hardware, tokenization/decoding, TTFT, tokens/s, VRAM, quality and schema behavior. Preserve cloud generation as an explicit comparison profile. Add durable structured safety events and injection tests.

Evaluate 30–50 prompts with native review across Gujarati/Hindi/English/code-mix in a separately configured lab. Live Roma remains Hindi-base Hinglish.

## Existing reuse in Voice_Agent

Canonical `ConversationStage`, domain state-machine facade, `route_intent()`, time resolver, prompt assembly, Indic normalization and final speech safety; Redis checkpoint restore remains the live hot path. Level 2 section 10 adds PostgreSQL latest-checkpoint save/restore through `PostgresConversationStateStore`, backed by `conversation_states` revision/schema/policy fields. Section 11 adds `Qwen3TransformersLLM`, the owner-authorized pinned Qwen3-1.7B INT4 `Qwen3MLXLLM` Mac development candidate, `TextConversationService`, console and language-evaluation runners, local-only settings and token-level timing. Structured safety audit and real 8B/native evidence remain pending. See [direct local inference](../25-local-transformers-llm.md).

## Acceptance gate

- [x] Section 10 state-machine facade and latest checkpoint save/restore preserve stage/slots without inventing a booking.
- [x] Section 11 text-flow console wired to the provider/FSM/extraction/safety/checkpoint contracts; mock smoke verified.
- [x] 40-case multilingual corpus and agreed per-language score gate; native review cannot be auto-filled.
- [x] Pinned Mac 1.7B INT4 inference works without external AI calls; synthetic extraction validates and the next field stays code-owned.
- [ ] Blocked topics, prior quotes and prompt injection use safe substitution; safety events are PII-safe.
- [ ] Native language-quality review and model/hardware limitations are recorded separately from live policy.

## Boundaries and advanced work

No telephony/audio integration, Ollama-first shortcut, vLLM, model-owned FSM or LLM safety decisions.

## Evidence

Section 10 implementation is in source with these fresh checks: state-machine API tests passed **17/17**, focused PostgreSQL checkpoint tests passed **2/2**, Ruff passed across `roma`, `tests` and `scripts`, configured mypy passed, `make ci` passed its native-service/migration/mock-provider/API slice, the full offline suite passed **1,674 tests with 23 warnings**, and Markdown link/anchor verification covered **63 non-archive Markdown files and 274 local links/anchors with zero issues**.

This section 10 evidence proves the finite-state-machine facade, route classification seam and compatible latest-checkpoint save/restore. It does not prove real Qwen3-8B inference, structured safety-event audit, native language review, redial/session identity or L7 recovery drills. Use the [verification contract](../12-verification.md), [baseline](../roadmaps/level-00-baseline.md) and [decision register](../decisions.md).

## Section 11 implementation and verification

The adapter uses direct Transformers tokenization/device placement/decoding, lazy optional SDK loading, non-thinking Qwen chat templates, configured completion/prompt limits and serialized cancellable generation. Terminal chunks carry model/revision, prompt/generated token counts, TTFT, tokens/second, load time and CUDA memory when applicable. The text service reuses controller/date/guardrail code with validated local extraction; the console displays every requested turn boundary. No new migration or working-database change is needed.

The multilingual lab has 40 synthetic prompts across all seven stages and Gujarati/Hindi/English/code-mix. On 2026-10-07 the owner agreed mean correctness/naturalness ≥4/5 **per language**, every score ≥3/5 and zero unsafe final replies. Native reviewer assignment and actual scores are pending. The current live policy stays Hindi-base Hinglish.

Initial section 11 offline evidence on 2026-10-07: the full suite passed **1,705 tests, 23 existing warnings**, including loading/admission checks. The expanded CI test slice passed **59 provider/config tests** and **21 unit/API/architecture/evaluation tests (1 existing warning)**. Ruff passed; mypy passed across **21 source files**. All pre-commit hooks passed across files. Markdown verification checked **64 non-archive Markdown files and 287 local links/anchors with zero issues**. Console mock smoke displayed the full trace, mock lab generation produced **40 outputs**, and unscored native review correctly exited nonzero. Existing `.env` HF credential alias was recognized without exposing its value.

Hardware evidence: `sysctl -n hw.memsize hw.machine` reported **8,589,934,592 bytes and arm64**. Qwen3-8B full precision is not admitted on this host; no 8B TTFT, throughput or native quality score is claimed. The locked optional runtime installed (Torch **2.14.1**, Transformers **4.57.6**). Real `check_transformers_runtime.py` smoke passed with a tiny random-weight CPU/FP32 Qwen3 model: five prompt tokens, three generated tokens, first-token timing and terminal metrics present. This is SDK-mechanics evidence only. A real runtime admission check selected MPS/FP16 and refused the 8B load before any weights/download. The [local LLM guide](../25-local-transformers-llm.md) owns commands/precision/score procedure. Section 12 durable structured safety events, suitable-host/native review, live integration and wider recovery evidence remain open.

## Mac development candidate verification

On 2026-10-07 the owner authorized a smaller INT4 candidate. `configs/local-llm-mac.json` pins `mlx-community/Qwen3-1.7B-4bit` at `3b1b1768f8f8cf8351c712464f906e86c2b8269e` through `Qwen3MLXLLM`. Its **968,080,210-byte** weight file was verified against upstream SHA-256 `0e86d9677e519323849eac1bc272caae88567a481ff188c431f70be543d9995f`. Locked MLX **0.32.3**, MLX LM **0.29.1** and Transformers **4.57.6** install and synchronize offline. Direct Transformers remains available separately; the Mac backend does not claim trained 8B execution.

Actual trained generation and synthetic controller smoke passed: the model extracted `Amit`, the controller remained in DISCOVER, the next status question was code-owned, final safety ran, and `booking_committed=false`. The documented `make text-console` command was exercised with synthetic stdin. Loading/decoding now share one dedicated SDK worker; repeat-request, timeout, prefill, cancellation, cache/revision, quantization-format and sanitized-error checks are covered. Clear validated profile answers avoid a second wording generation; query cues and mixed questions retain model wording.

The **initial 40 real outputs** cover ten cases per language. Four contradictory city fixtures were corrected to caller education/year answers and regenerated. All retained replies were reused only after exact captured-prompt comparison; missing-information cases remain intact. Scoring rejects stale prompts and correctly returns `pending-native-review` for the owner-only unscored artifact. No native scores are manufactured. Samples still include wrong-language, field-label and task-following replies: **1.7B is a development baseline, not an approved production model**. Native review plus prompt/model improvement and controlled latency evidence remain required before promotion.

A quiet short trained smoke (54 prompt tokens, 9 generated tokens) measured **1.06 s load, 469.6 ms TTFT and 15.15 generated tokens/s**, including prefill. Full stage prompts approach 5,000 tokens; the initial 40-case run observed median TTFT around **16.3 s**, with other checks/inference competing during portions of that run. These are single-host observations, not voice/capacity SLOs. Longer prompts need optimization or a suitable host before live use. Optional MLX LM currently emits an SDK `mx.metal.device_info` deprecation message; it does not block inference.

Initial Mac-profile offline checks: **1,727 tests passed with 23 existing warnings**; bounded CI passed **77 provider/config tests** and **25 unit/API/evaluation tests (1 existing warning)**. Ruff and mypy (**22 source files**) passed; all pre-commit hooks passed. Markdown verification covered **64 non-archive files, 290 local links/anchors, zero issues**. Model weights, secrets and review artifacts stay outside Git. No migration, working-database change or live carrier call was made.

## Compact local prompt and comparison follow-up

On 2026-10-07 the owner authorized compact current-stage prompts, MLX immutable-prefix caching, validated critical extraction and a pinned 1.7B/4B text comparison. Native scoring, realistic audio workload, end-of-speech-to-first-audio and complete voice/interruption approval are explicitly deferred to later phases.

The provider-aware prompt builder lives in services, preserving the enforced domain dependency direction. The final 40 requests use 302–400 actual chat-template tokens with 1.7B and 298–396 with 4B; the old full-manual captures are historical and cannot be scored against this generation. Profiles keep a 1,024-token rejection ceiling without truncating safety. The 1.7B default remains explicit; the additional 4B profile pins revision `50d427756c6b1b2fe0c0a10f67fbda1fc8e82c1b`. Separate JSON-only extraction requests precede controller decisions. Conservative full name/year declarations and existing next-question/readback code avoid unnecessary inference.

Cached and uncached trained greedy generation produced identical text in each model's mechanics smoke, including a verified warm cache hit. Synthetic ambiguous-name extraction returned Amit, the controller retained DISCOVER, the next question remained code-owned and no booking was committed. Offline regression checks cover distinct caller suffixes, policy/revision invalidation, bounded entries, failure isolation, disabled caching, token-boundary mismatch, delayed extraction, malformed JSON and Unicode fallback. The comparison runner rejects stale prompts/decoding settings, mismatched profiles and incomplete measurements. A trained 1.7B time probe exposed fabricated day/offer/readback fields, then copied a numeric example incorrectly; it is not accepted evidence of reliable time extraction. Context checks now reject conflicting day fields, nonexistent offer choices and readback affirmation outside CLOSE. The misleading numeric example was removed. A final greedy 4B probe still returned `period=morning` for `kal teen baje aaunga` despite an explicit instruction to omit unstated periods; the controller accepted no slot and committed no booking. Time-extraction quality remains unresolved. Neither a valid schema nor these synthetic probes establish semantic quality. See [the local inference contract](../25-local-transformers-llm.md#reproducible-17b-versus-4b-text-comparison).

### Final text-only comparison evidence

The final 2026-10-07 comparison used the same 40 cases per model, compact prompts, temperature 0.7, top-p 0.8 and top-k 20. Earlier runs using MLX's default filtering are superseded. Each candidate ran in its own process. These are single-host observations under the current desktop workload; they are not controlled capacity or voice-latency SLOs.

| Pinned Apple candidate | Prompt tokens | Model TTFT median / p95 | Warm-cache median / p95 (36 cases) | Peak MLX allocator |
| --- | --- | --- | --- | --- |
| Qwen3-1.7B INT4 | 302–400 | 431.18 / 896.13 ms | 426.18 / 522.74 ms | 1,378,699,548 bytes |
| Qwen3-4B-Instruct-2507 INT4 | 298–396 | 1,146.72 / 2,627.06 ms | 1,124.28 / 1,390.57 ms | 2,786,540,812 bytes |

Both models had 36 cache hits and four misses (one immutable language prefix per group). The 1.7B run had five completion-budget exhaustions and four raw replies containing replacement characters; 4B had two of each. Invalid Unicode uses the existing safe fallback. Samples still show task/language problems, including fee wording and unnecessary slot questions. Existing visit-time guards can also replace class-schedule wording, so raw and final replies must both be reviewed in the later task-quality phase. No native scores were assigned.

During 4B, system-wide swap usage changed from 5,488.56 to 6,328.00 MiB on this already-swapping 8 GiB host. This is not usage attributable solely to Roma; process RSS and the MLX allocator must not be added together. It does not establish headroom for concurrent audio. Keep 1.7B as the explicit development default and 4B as an optional text candidate; neither is approved for production. The 4B **2,263,022,417-byte** weight file was verified against SHA-256 `2a73c6c248601ab904e035548abd8e6abb65ea27dcb5f342fb0a8910eb44173f`. Its separate chat-template data is included in setup and required at load; missing templates fail with a setup instruction.

Owner-only captures are under ignored `var/roma/llm-lab/compact-comparison/`; `summary.json` can be rebuilt with `--summarize-only`, which verifies current prompts, sampling settings, per-case measurements and pinned identities. No model weights, secrets or captures enter Git. Full voice approval, native scoring and realistic audio-load/interruption measurements remain deferred.

Final offline verification: **1,758 tests passed with 23 existing dependency warnings**. The bounded mocked CI slices passed **86 provider/config tests** and **47 unit/API/architecture/evaluation tests (1 existing warning)**; Ruff and mypy (**24 source files**) passed; all pre-commit hooks passed across files. The lockfile check resolved 145 packages consistently. Markdown verification covered **64 non-archive files and 295 local links/anchors with zero issues**. `--summarize-only` validated both final 40-case artifacts. No migration or working-database change was made; full voice approval remains pending by owner instruction.

Return to the [documentation index](../README.md).
