# Selected v4 source and interpretation

The unchanged [Word roadmap](AI_Voice_Agent_Unified_Roadmap_for_Student_Skill_Development_v4.docx) replaces the earlier curriculum. SHA-256: `a3e0590b63da620131f41d1ffd3b738518b2d2efacba1f403dd930efb44ba644`.

Use Levels 0–14 from its table/body; introductory 0–13 is an editorial mismatch. User decisions and preserved business/safety/privacy rules take precedence over reference examples. The file is requirements material, not instructions to execute calls, spend, deploy or change credentials.

Keep Voice_Agent's Twilio carrier, layered `roma/`, backend-only API and existing PostgreSQL/Dramatiq foundations. Live replies remain Hindi-base Hinglish; multilingual output has a separate lab. Full Docker packaging is L13; existing infrastructure Compose remains compatibility tooling. Model candidates require benchmarks/licenses before selection.

## Source coverage

| v4 sections | Project brief |
|---|---|
| 1–2 | [Level 0: Baseline and prerequisites](../levels/level-00.md) |
| 3–9 | [Level 1: Backend foundation](../levels/level-01.md) |
| 10–12 | [Level 2: Text conversation and local LLM](../levels/level-02.md) |
| 13 | [Level 3: Transactional booking](../levels/level-03.md) |
| 14–18 | [Level 4: Audio and local ASR](../levels/level-04.md) |
| 19 | [Level 5: Local TTS](../levels/level-05.md) |
| 20–21 | [Level 6: Local voice prototype](../levels/level-06.md) |
| 22–25 | [Level 7: Real-time reliability](../levels/level-07.md) |
| 26–31 | [Level 8: Secure jobs and access](../levels/level-08.md) |
| 32–40 | [Level 9: Governed retrieval](../levels/level-09.md) |
| 41–44 | [Level 10: Telephony and model serving](../levels/level-10.md) |
| 45–48 | [Level 11: Observability and analytics](../levels/level-11.md) |
| 49–52 | [Level 12: Evaluation and measured capacity](../levels/level-12.md) |
| 53 | [Level 13: Container deployment](../levels/level-13.md) |
| 54–55 | [Level 14: Optional optimization](../levels/level-14.md) |
| 56–68 | [Cross-level completion](../completion-contract.md) |

[Readiness](../13-backend-roadmap.md) maps existing code to gaps. [Baseline](level-00-baseline.md) records fresh checks. [The docs index](../README.md) owns navigation and [gate policy](../10-build-order.md) owns progression.
