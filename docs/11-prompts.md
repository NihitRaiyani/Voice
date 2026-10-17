# Runtime prompts and provider contract

Runtime text in `roma/prompts/` changes product behavior. The Level 1 persistence increment preserves that text. `ConversationStage` names and `stages/*.md` stay synchronized with the controller/checkpoints/tests.

## Assembly and speech

`roma/domain/conversation/prompts.py` assembles stable `persona + hard_rules`, then the current stage/context and a bounded history tail. Branch is fixed within the call. Per-turn values belong outside the stable prefix; provider prefix caching is an optimization, not business authority.

Current caps are open 25, discover 20, value 120, structure 60, pivot 30, objection 45 and close 25 words. Token ceiling is four times the word cap; tokens are not exact word counts. Do not restore the old 70-word value cap or truncate TTS to imitate enforcement.

Live speech remains Hindi-base Hinglish with feminine Roma self-reference and respectful caller address, short relevant turns and no invented facts. Separate multilingual lab configuration requires native review and does not change live policy.

## Code authority

Code supplies stage, pending question, valid proposals, facts and confirmation state. Models cannot advance stages, perform relative-date arithmetic, reserve slots, authorize consent or override safety. Main wording and bounded structured extraction both need provider interfaces; migrate neither implicitly.

At L3 reconcile static always-available prompt statements with actual availability/conflict/commit behavior. At L10 validate inbound/outbound opener state and shared prompts together. Final success wording must reflect committed database truth.

## Knowledge and evaluation

Approved facts/direct safe responses remain the baseline. L9 introduces separately presented eligible evidence, grounding and no-evidence deferral. Caller/retrieved instructions cannot change hard rules. Booking/control/safety bypass retrieval.

Version model/prompt/policy and document identity in benchmark reports. Test quote injections, wrong-stage questions, repetition, invented learner facts, stale confirmation and code-mix speech. Changed fixed lines require matching audio/cache manifests. See [conversation](03-stage-machine.md), [safety](04-guardrails.md) and [verification](12-verification.md).

## Section 11 text seam

`roma/services/conversation_prompts.py` builds a narrow current-stage request from existing approved facts, the controller task and selected state fields. Names remain locally parsed; known names and recognized phone/hash/email/recording identifiers are removed from outbound text. There is no full state dictionary or caller identity in SDK metadata. `OpenAILLM` applies another recognized-identifier filter at the API boundary. This is minimization, not proof that arbitrary prose is free of every kind of personal data.

The text service awaits extraction before controller decisions and applies existing confirmation/time/sign-off/safety rules to final replies. It discards partial failed generation. This does not rewrite the legacy Pipecat prompts or claim that their existing caller-name/extraction flow now satisfies the new target privacy contract. Later live integration must apply and verify this boundary there too. See [the OpenAI contract](25-openai-llm.md).


Section 12 validates caller length/characters and prompt-bound slots in both the text seam and existing live prompt/extraction boundaries. Branch/field/time domains remain code-owned; free-text slots preserve Indic marks but reject control markup and instruction-like values. Recognized override attempts skip generation and receive audited safe speech. This strengthens prompt isolation without claiming that lexical canaries detect every semantic attack or that the legacy history is universally PII-free. See [input limits](04-guardrails.md#untrusted-inputs-and-limits).
