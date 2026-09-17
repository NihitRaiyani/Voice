# Skill Sync Prompts

For each chosen skill: what it does in Roma, WHEN to invoke it, and a paste-ready prompt for
Claude Code. Paste the prompt into Claude Code at the moment named. Invocation is tied to a
build step — never ambient.

---

## skill-creator — turn specs into enforced skills
**When:** once, before Step 3, and again whenever a `docs/` contract changes.
**Why:** the filter rules, phase contract, and objection bank must be enforced every session,
not re-explained. skill-creator packages them so they auto-load.

> Prompt to Claude Code:
> "Using skill-creator, create three Roma skills from this repo's docs:
> (1) `roma-guardrail` from docs/04-guardrails.md — the four block categories, the allow-case,
> the prior-quote defense, and the substitution lines. It must trigger on any code touching
> the pre-TTS filter or LLM→TTS path.
> (2) `roma-phase-machine` from docs/03-phase-machine.md — the 7 phases, transitions, word
> caps, and slot-extraction rules. Trigger on the conversation controller.
> (3) `roma-endpointing` from docs/05-endpointing-vad.md — the VAD/endpointing/barge-in config
> values. Trigger on pipeline/VAD code.
> Keep each skill's description tight so it triggers only on its own area. Do not invent rules
> not in the docs."

---

## context7 — current library docs before coding an API
**When:** immediately before writing any code against Pipecat, Twilio, Sarvam, or OpenAI.
**Why:** the Pipecat turn-taking / interruption API changes fast; stale memory = wrong code.

> Prompt to Claude Code:
> "Before writing any Pipecat pipeline code, use context7 to pull the CURRENT Pipecat docs for:
> the processor/pipeline API, Silero VAD analyzer, the interruption/barge-in lifecycle, and the
> Twilio transport. Confirm the smart-endpointing / turn-analyzer interface exists in the
> installed version before using it. Do the same for the Twilio Media Streams `clear` message
> and the Sarvam Saaras/Bulbul streaming APIs. Code against what context7 returns, not memory."

---

## code-review — guard the concurrency hot path
**When:** on every PR/diff touching barge-in, cancellation, the filter, or multi-call state.
**Why:** these are where race conditions and audio-leak bugs hide.

> Prompt to Claude Code:
> "Run code-review on this change with a focus on the Roma barge-in/cancellation path. Check
> specifically: is cancellation idempotent (double barge-in safe)? does it propagate in order
> (stop LLM → flush filter → flush Bulbul → Twilio clear)? can a half-generated blocked line
> leak to TTS during teardown? is any per-call state accidentally shared across calls? is any
> secret or lead PII logged? Flag anything that could cause overtalk, leaked audio, or a filter
> bypass."

---

## code-simplifier — enforce the discipline
**When:** after any module grows past its single responsibility, before merging.
**Why:** matches the "justify every layer or delete it" rule. We already dropped LangGraph on
this principle.

> Prompt to Claude Code:
> "Run code-simplifier on this module. The Roma discipline is: simplest thing that works, one
> responsibility per module, prefer a dict + match over a framework, no abstraction we can't
> justify. Flag over-engineering, premature generalization, and any framework pulled in for
> something a plain function would do. Do not simplify away the guardrail or the fail-safe."

---

## superpowers — multi-step planning within a phase
**When:** at the start of a build step that has several interdependent parts (e.g. Step 5,
barge-in + concurrency).
**Why:** keeps a multi-part step ordered and verifiable instead of hacked together.

> Prompt to Claude Code:
> "Use superpowers to plan Step 5 (barge-in + concurrency hardening) from docs/10-build-order.md
> into an ordered, checkable sequence. Each step must be independently testable. Respect the
> ordering constraint that cancellation propagates stop-LLM → flush-filter → flush-Bulbul →
> Twilio-clear. Surface risks before writing code."

---

## andrej-karpathy-skills — the build-discipline lens
**When:** load first; transcribe its rules into CLAUDE.md before Step 1.
**Why:** you asked to hold the whole build to Karpathy's discipline. That requires reading the
actual rules, not assuming them.

> Prompt to Claude Code:
> "Load andrej-karpathy-skills and show me its actual rules/skills. Then append them verbatim
> to the 'Build discipline' section of CLAUDE.md, and from then on evaluate every Roma build
> decision against them. If any existing decision in docs/ conflicts with a Karpathy rule, flag
> the conflict — don't silently override either."

---

## context-mode — HOLD (justify first)
**When:** only if it fills a gap the existing memory/CLAUDE.md setup doesn't.
> Prompt to Claude Code (diagnostic, before adding):
> "Show me what context-mode manages and how it differs from this repo's CLAUDE.md + memory
> setup. If it overlaps rather than adds, recommend skipping it to avoid two context systems
> drifting. Only propose wiring it in if you can name a concrete gap it closes."
