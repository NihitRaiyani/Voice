# Project Skills and Invocation Rules

Skills exist to protect a real project boundary or make a repeated workflow more reliable. Do not
collect skills for technologies that are only mentioned in the roadmap.

## Active project-local skills

| Skill | Load when | Protects |
|---|---|---|
| `roma-guardrail` | Spoken output, LLM-to-TTS, safety lexicons, sentence flushing, cancellation | No generated speech bypasses deterministic safety |
| `roma-backend-roadmap` | Architecture, persistence, APIs, booking, jobs, security, observability, testing, delivery | One staged backend increment; current/planned truth remains accurate |

Project-local skills live under `.claude/skills/`. Their instructions are versioned with the
repository and must be updated when the corresponding contract changes.

## Supporting workflow skills

Use available workflow skills only when their trigger matches the task:

- **brainstorming / writing-plans:** before a meaningful behavior or architecture change;
- **systematic-debugging:** when behavior is broken and the cause is unknown;
- **test-driven development:** for a requested test-first implementation;
- **code review:** for a diff review, especially cancellation, shared state, transactions, and auth;
- **verification-before-completion:** before claiming a change works;
- **skill-creator:** when adding or materially revising a project-local skill;
- **current official documentation lookup:** before coding against fast-changing provider APIs.

## Wiring rule

A skill earns a place only if it changes a decision or prevents a demonstrated failure. Invocation
must be tied to the task, not ambient. Roadmap technologies do not each need their own skill.

## Skills deliberately not added

- Frontend/design skills: this is a backend-only learning project.
- Microservice/Kafka/Kubernetes skills: no demonstrated need yet.
- Generic context or abstraction frameworks: use only when a concrete repository problem appears.
- A separate skill for every provider: prefer narrow adapters and authoritative provider docs.
