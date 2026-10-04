# Level 6 — Local voice prototype

**v4 sections:** 20–21. **Status:** Planned; no local microphone/model demo performed.

## Entry gate

Levels 2, 3, 4 and 5 core gates.

## Scope when implementation is requested

Connect microphone → local ASR → deterministic text controller/booking → local LLM/safety → local TTS → speaker. Use headphones for the microphone experiment. Save approved test turns and structured stage timings for replay.

Use correlation IDs, language, policy/model identity and safe timing fields in logs. Keep raw PII and ordinary transcript content out of general logs. Demonstrate the pipeline without external GenAI calls.

## Existing reuse in Voice_Agent

Domain turn controller, PostgreSQL booking foundation, safe speech and local provider contracts from earlier levels.

## Acceptance gate

- [ ] End-to-end local lab handles supported language inputs and the approved live output profile.
- [ ] Confirmed booking reflects database commit; unsafe output is substituted.
- [ ] Saved approved turns reproduce measured stage timing and known failures without exposing PII.

## Boundaries and advanced work

No real phone call, public endpoint, echo-processing assumption or unbounded capture retention.

## Evidence

Record revision, reproducible commands, environment/data/configuration identity, actual outcomes, demo/reviewer result and limitations here when this level is implemented. Existing foundations do not automatically pass the full gate. Use the [verification contract](../12-verification.md), [baseline](../roadmaps/level-00-baseline.md) and [decision register](../decisions.md).

Return to the [documentation index](../README.md).
