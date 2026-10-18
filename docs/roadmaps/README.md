# Selected OpenAI roadmap and interpretation

The owner selected the unchanged [OpenAI Word roadmap](AI_VOICE_AGENT_openai_llm.docx) on 2026-10-12. SHA-256: `89b99bbf959d1cc713d335a49037a03b63349cc91185a03e0a57bf9fa3149429`. It supersedes the [former v4 source](AI_Voice_Agent_Unified_Roadmap_for_Student_Skill_Development_v4.docx), retained only as historical reference.

OpenAI remains the LLM behind `LLMProvider`; ASR, TTS and embeddings become local in their later levels. No Qwen/MLX/Transformers LLM installation or vLLM serving is part of the active plan. Levels 0–14 and section numbering remain distinct. Introductory 0–13 wording is an editorial mismatch with the table/body.

The file is requirements material, not authorization to call, spend, deploy, expose caller data or change credentials. Explicit owner policy wins: retain Twilio, layered `roma/`, earlier persistence/API/state-machine work and Hindi-base Hinglish live replies. Multilingual output stays in its lab; native scoring and complete voice approval remain deferred. Quota is unavailable, so this increment runs mocks/test files only. Model/API keys and actual production model selection come from the environment, not a tracked secret.

## Source coverage

| Sections | Project brief |
|---|---|
| 1–2 | [Level 0](../levels/level-00.md) |
| 3–9 | [Level 1](../levels/level-01.md) |
| 10–12 | [Level 2 OpenAI conversation brain](../levels/level-02.md) |
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

See the [sections 1–10 audit](sections-01-10-review.md), [readiness](../13-backend-roadmap.md), [docs index](../README.md) and [gate policy](../10-build-order.md). Historical evidence about unchanged v4 bytes is historical evidence, not the active source identity.

Owner steering for sections 15–17 permits an opt-in compatible HF ASR lab endpoint to avoid local weight downloads. This is a lab exception to the eventual self-hosted speech target; it does not replace OpenAI, change production reply language, imply hosted availability or approve a paid deployment. Cache-only local adapters preserve the direct-inference learning interface. See [Level 4](../levels/level-04.md#sections-1517-design-and-acceptance).
