# Skills — which, why, and the wiring rule

Only skills that touch THIS build (a Python real-time voice pipeline) are included. Web/TS/
cloud skills (cloudflare, supabase, typescript-lsp, frontend-design, figma, playwright) are
deliberately excluded — wrong stack. github/docker are deferred until containerization.

Per-skill invocation prompts (the exact WHEN + paste-ready text) live in `sync-prompts.md`.

## Chosen
| Skill | Role in Roma |
|---|---|
| **skill-creator** | Turn the `docs/` specs (filter rules, phase contract, objection bank) into auto-loading skills so they are ENFORCED every session, not remembered. |
| **context7** | Pull CURRENT Pipecat / Twilio / Sarvam / OpenAI docs before coding any API. The Pipecat turn-taking API changes fast — never code against stale memory. |
| **code-review** | Run on every change to the barge-in / cancellation / filter path — where concurrency bugs live. |
| **code-simplifier** | Run after any module outgrows its job. Enforces the "justify every layer" discipline. |
| **superpowers** | Multi-step build planning within a phase. |
| **andrej-karpathy-skills** | The build-discipline lens (simple, first-principles, no cargo-cult). Load it, transcribe its rules into `CLAUDE.md`, then hold the build to them. |

## Debated — resolved: HOLD
- **context-mode:** the "two context managers risk drift" worry is miscategorized —
  CLAUDE.md + memory manage *decisions*; context-mode manages *byte volume* (runs a
  command/fetch in a sandbox, indexes the raw output, surfaces only the derived answer). It
  can't drift against CLAUDE.md because it doesn't hold decisions. But by the wiring rule it
  still earns nothing yet: this repo is docs-only, so there is no large output to keep out of
  context. **Verdict: HOLD.** Wire it in only when the running pipeline emits high-volume
  artifacts you'd otherwise read into context — Pipecat frame/latency traces, Twilio μ-law
  media dumps, multi-call soak-test output. Not before. See `sync-prompts.md`.

## The wiring rule
A skill earns a slot only if it changes what gets built or how. Don't collect skills. Each
entry in `sync-prompts.md` says exactly WHEN Claude Code should invoke it — invocation is
tied to a build step, not left ambient.
