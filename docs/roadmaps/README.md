# Selected Claude roadmap and interpretation

The owner selected the unchanged [Claude Word roadmap](AI_VOICE_AGENT_claude_llm.docx) on 2026-10-20. SHA-256: `67a79ba87fbec07051d460fd4ec9d5520f7047c0e242da72a65bfdd09ede5f48`. It replaces the [OpenAI source](AI_VOICE_AGENT_openai_llm.docx) selected on 2026-10-12 (SHA-256 `89b99bbf959d1cc713d335a49037a03b63349cc91185a03e0a57bf9fa3149429`), which remains historical alongside [v4](AI_Voice_Agent_Unified_Roadmap_for_Student_Skill_Development_v4.docx). All Word files are preserved byte-for-byte; source snippets are requirements examples, not permission to rewrite foundations.

Claude Haiku 5.5 (`claude-haiku-5-5`) at low effort is the main LLM behind `LLMProvider`, with the official async Anthropic SDK and private `ANTHROPIC_API_KEY`. No temperature/top_p/top_k overrides. The owner authorizes synthetic paid Claude verification; automated tests remain mock/offline and CLI paid runs require explicit opt-in. No OpenAI requests or fallback. Local ASR/TTS/embeddings remain the target; carrier/internal services and the explicitly permitted public HF ASR labs retain their existing boundaries.

Explicit owner policy preserves Twilio, layered `roma/`, migrations, PostgreSQL/Redis/booking/API/state foundations, deterministic safety and Hindi-base Hinglish live replies. Multilingual generation is a separate lab. Only the pinned Whisper base ASR artifact is approved; the completed 15-case local benchmark failed Gujarati/Hindi/stitched quality. Local TTS is installed, and one English physical microphone/mock-LLM/TTS turn was heard clearly. These results do not pass native/representative or complete Level 6 gates. Current alignment scope ends at section 20; section 21 remains unimplemented.

## Source coverage

| Sections | Project brief |
|---|---|
| 1–2 | [Level 0](../levels/level-00.md) |
| 3–9 | [Level 1](../levels/level-01.md) |
| 10–12 | [Level 2 Claude conversation brain](../levels/level-02.md) |
| 13 | [Level 3](../levels/level-03.md) |
| 14–18 | [Level 4](../levels/level-04.md) |
| 19 | [Level 5](../levels/level-05.md) |
| 20–21 | [Level 6](../levels/level-06.md) |
| 22–25 | [Level 7](../levels/level-07.md) |
| 26–31 | [Level 8](../levels/level-08.md) |
| 32–40 | [Level 9](../levels/level-09.md) |
| 41–44 | [Level 10](../levels/level-10.md) |
| 45–48 | [Level 11](../levels/level-11.md) |
| 49–52 | [Level 12](../levels/level-12.md) |
| 53 | [Level 13](../levels/level-13.md) |
| 54–55 | [Level 14](../levels/level-14.md) |
| 56–68 | [Cross-level completion](../completion-contract.md) |


[The sections 1–20 audit](sections-01-10-review.md#claude-source-review-through-section-20--2026-10-20), [readiness](../13-backend-roadmap.md), [Claude contract](../25-claude-llm.md) and [gate policy](../10-build-order.md) distinguish implemented foundations from acceptance. Levels 0–14 and section numbering are distinct; introductory 0–13 wording is an editorial mismatch. Historical OpenAI/HF results retain their dates/models and cannot prove Claude latency, cost or native quality.
