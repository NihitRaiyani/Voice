# Project Skill Invocation Examples

These examples show when to load the project-local skills. They are guidance, not permission to
expand a task's scope.

## `roma-guardrail`

**When:** any change can affect the text or audio Roma speaks.

> Load `.claude/skills/roma-guardrail/SKILL.md`. Verify every generated line still passes the
> deterministic pre-TTS filter, cancellation cannot leak a partial blocked line, and failure falls
> back to safe speech rather than raw model output. Keep tests and `docs/04-guardrails.md` aligned.

## `roma-backend-roadmap` — persistence slice

**When:** designing or implementing PostgreSQL, SQLAlchemy, Alembic, repositories, or durable call
state.

> Load `.claude/skills/roma-backend-roadmap/SKILL.md`. Select one vertical slice, mark its current
> status, define the durable invariant and Redis/PostgreSQL boundary, then design migrations and
> integration evidence. Do not move realtime frame/token data into PostgreSQL.

## `roma-backend-roadmap` — appointment concurrency slice

**When:** working on booking, rescheduling, cancellation, slots, or calendar coordination.

> Load `.claude/skills/roma-backend-roadmap/SKILL.md`. State the one-slot/one-booking invariant,
> transaction and locking strategy, rollback behavior, and HTTP conflict mapping. Require a
> concurrency test with one winner before marking the module implemented.

## `roma-backend-roadmap` — security/API/job slice

**When:** working on versioned APIs, auth/RBAC, webhooks, rate limits, workers, or retries.

> Load `.claude/skills/roma-backend-roadmap/SKILL.md`. Draw the trust and idempotency boundaries,
> keep provider verification separate from human auth, define duplicate/crash behavior, and avoid
> exposing provider payloads or PII through the public contract.

## `roma-backend-roadmap` — observability/testing/delivery slice

**When:** working on logs, metrics, traces, load tests, containers, or CI/CD.

> Load `.claude/skills/roma-backend-roadmap/SKILL.md`. Start from the question the signal answers,
> define correlation and PII rules, keep automated tests free of paid calls, and demand measured
> evidence before making scalability claims.

## Current provider documentation

**When:** immediately before writing against Pipecat, Twilio, Sarvam, OpenAI, or another external
SDK/API.

> Read the installed version and current official provider documentation for the exact operation.
> Confirm callback/signature, streaming, timeout, and error semantics. Keep the adapter narrow and
> preserve fakes for offline tests.
