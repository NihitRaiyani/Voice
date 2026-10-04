# Level 5 — Local TTS

**v4 sections:** 19. **Status:** Cloud synthesis/cache present; local TTS lab planned.

## Entry gate

Level 1 provider/cache contract and approved fixed speech; may run as an isolated lab after prerequisites.

## Scope when implementation is requested

Implement candidate local Indic TTS behind the provider contract. Evaluate intelligibility, code-mix, voice consistency, first audio and real-time factor on approved text. Keep Hindi-base Hinglish live policy while multilingual output remains a lab profile.

Key cached audio by text, language, voice and model version; preserve manifest/text identity and invalidate changed speech. Test cold/warm synthesis and failure-safe substitution.

## Existing reuse in Voice_Agent

Bulbul/ishita comparison service, phrase manifests, safe canned assets and audio sanitizer.

## Acceptance gate

- [ ] Actual local synthesis with measured first-audio/RTF and native intelligibility review.
- [ ] Cache hit/invalidation and cross-language/voice/model isolation verified.
- [ ] Failure and stale-clip cases never speak unapproved or mismatched text.

## Boundaries and advanced work

No silent replacement of live voice, paid bulk rerendering, local-model telephony integration or serving framework.

## Evidence

Record revision, reproducible commands, environment/data/configuration identity, actual outcomes, demo/reviewer result and limitations here when this level is implemented. Existing foundations do not automatically pass the full gate. Use the [verification contract](../12-verification.md), [baseline](../roadmaps/level-00-baseline.md) and [decision register](../decisions.md).

Return to the [documentation index](../README.md).
