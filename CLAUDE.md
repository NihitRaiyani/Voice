# CLAUDE.md — Roma build working memory

Read this before doing anything in this repo. It is the source of truth for how to build.

## What Roma is
Outbound code-mix (Gujarati/Hindi/English) voice agent that books a **specific day+time**
counselling visit for Weltec's Digital Marketing course. A soft "dekhta hoon" is NOT a win.

## The stack (locked — do not substitute)
| Layer | Choice |
|---|---|
| Telephony | Vobiz (numbers + `<Stream>` WebSocket, 8kHz μ-law, separate legs per speaker) |
| Orchestration | Pipecat (owns pipeline, VAD, interruption lifecycle) |
| VAD | Silero (model-based — NOT an energy/dB gate) |
| STT | Sarvam Saaras (endpoint-on-final, code-mix mode) |
| LLM | OpenAI (one call/turn, streamed, sentence-chunked) |
| TTS | Sarvam Bulbul (persistent single socket, code-mix mode) |
| State / cache / queue | Redis |
| Deploy | Vadodara (self-hosted) — verify RTT to Vobiz Mumbai edge |
| Post-lock | async queue → CRM/calendar (never inside the turn) |

**Explicitly rejected:** LiveKit (Pipecat covers orchestration), LangGraph (the 7 phases are
conversation state, not a per-turn execution graph). Do not reintroduce either without a
written justification in `docs/decisions.md`.

## Non-negotiable gates (build stops if these are missing)
1. **The pre-TTS filter ships with the first callable build.** No call goes out without it.
   See `docs/04-guardrails.md`. It is a router (substitutes a safe line), not a censor.
2. **No secret ever in code or logs.** All keys via env/secret store. See `docs/07-security.md`.
3. **No cert claim beyond the Weltec certificate** until D1 is answered.
4. **Recording consent** is stated at call start; recordings are access-controlled.

## Build discipline (Karpathy-aligned)
> The `andrej-karpathy-skills` plugin is loaded but its exact rules are not yet transcribed
> here. When you read them, paste the rules into this section and hold the build to them.
> Until then, follow these principles which are consistent with that philosophy:
- Build the simplest thing that works; justify every layer or delete it.
- Understand each component end-to-end before wiring the next. No cargo-culting frameworks.
- Measure before optimizing. The latency budget is real; guess nothing, profile everything.
- Prefer a dict + `match` over a framework. We already dropped LangGraph for this reason.
- One turn = one LLM call. Do not add tool loops unless a phase provably needs one.

## Skill wiring (see skills/sync-prompts.md for the exact prompts)
- **skill-creator** → turn the specs in `docs/` (filter rules, phase contract, objection
  bank) into auto-loading skills so they are enforced, not remembered.
- **context7** → pull CURRENT Pipecat / Vobiz / Sarvam docs before coding any API. The
  Pipecat turn-taking API changes fast; do not code against stale memory.
- **code-review** → run on every PR touching the barge-in / cancellation / filter path
  (concurrency bugs live there).
- **code-simplifier** → run after any module grows past its job. Serves the discipline above.
- **superpowers** → use for multi-step build planning within a phase.

## Where to start
`docs/10-build-order.md`. Do not start with the LLM or the "fun" parts. Start with the
guardrail and the security scaffold — they gate everything else.
