# Local Code-Mix Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route explicit local Gujarati/Hindi-English turns across IndicConformer and Whisper base without paid calls, new weights or microphone use.

**Architecture:** Extend the existing local composite with a pure bounded energy boundary detector, Whisper language-ID tail arbitration and an exact domain-loanword normalizer. Preserve all monolingual behavior and keep the feature isolated from production telephony.

**Tech Stack:** Python 3.12, existing STTProvider contracts, AI4Bharat IndicConformer cache, faster-whisper base cache, pytest, Ruff, mypy.

---

### Task 1: Specify adaptive routing behavior

**Files:**
- Create: `tests/providers/ai/test_local_routing.py`
- Modify: `roma/services/speech_input.py`

- [x] Write a failing test proving saved mixed states emit `gu-en`/`hi-en` while monolingual states retain `gu`/`hi`/`en`.
- [x] Write failing public-interface tests for direct monolingual selection, mixed boundary fallback, no fallback on provider errors, one-letter abstention and two-letter Indic acceptance.
- [x] Run `uv run --no-sync pytest tests/providers/ai/test_local_routing.py tests/services/test_speech_input.py -q` and confirm the new expectations fail.
- [x] Implement only the mixed hint mapping needed by the tests.

### Task 2: Implement bounded span routing and normalization

**Files:**
- Modify: `roma/providers/ai/local_routing.py`
- Test: `tests/providers/ai/test_local_routing.py`

- [x] Add a pure 20 ms RMS detector that selects at most one internal gap, requires at least 600 ms of relative low energy and at least 800 ms of audio on each side.
- [x] Add exact-script token normalization for the reviewed Gujarati/Hindi booking loanwords, including adjacent Indic punctuation.
- [x] Implement Indic head decoding, Whisper language-ID tail arbitration and Indic tail retention when English evidence is not credible.
- [x] Run the focused tests until green, then run Ruff and mypy on changed files.

### Task 3: Benchmark the immutable corpus

**Files:**
- Modify: `scripts/asr_benchmark.py`
- Modify: `roma/eval/asr_benchmark.py`
- Modify: `roma/eval/benchmark_evidence.py`
- Modify: `tests/eval/test_asr_benchmark.py`
- Create: `benchmarks/asr/section18/adaptive-local-report.json`

- [x] Add an offline-only `adaptive_local` benchmark provider composed from the pinned Indic and Whisper caches.
- [x] Preserve report identity, manifest validation, atomic checkpoints and no-network metadata.
- [x] Run all 15 cases separately against the unchanged manifest and save actual WER, CER and wall latency.
- [x] Store evidence in the existing benchmark tables only if the report passes the existing evidence validator; do not alter schema or business data.

### Task 4: Synchronize operator and learning documentation

**Files:**
- Modify: `docs/levels/level-04.md`
- Modify: `docs/runbook.md`
- Modify: `docs/12-verification.md`
- Modify: `docs/17-placement-study-guide.md`
- Modify: `benchmarks/asr/section18/README.md`
- Modify: `CLAUDE.md`

- [x] Record the design, exact command, results and limitations without claiming native code-mix approval.
- [x] State that Claude and microphone checks remained deferred and production routing is unchanged.
- [x] Run the Markdown link/anchor checker.

### Task 5: Verify and publish

**Files:** all changed files above.

- [x] Run focused tests, full offline test suite, Ruff, mypy and pre-commit.
- [x] Verify tracked artifacts contain no secrets and `git status` has only scoped changes.
- [x] Commit once with a scoped message and push `main` under the owner’s standing authorization.

