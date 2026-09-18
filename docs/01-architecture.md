# 01 — Architecture

**Status:** Implemented layered modular monolith. PostgreSQL remains planned, not present.

**Learning objective:** Trace one call across transport, realtime processing, business control,
state, safety, and asynchronous work. Explain why latency-sensitive and durable work need
different boundaries.

## Runtime (one call)
```
 ┌──────────┐   dial     ┌─────────────────────────────────────────────┐
 │  Dialer  │──────────▶ │            Twilio (per-call)                 │
 │ (worker) │            │  Calls API → `/answer` → bidirectional `/ws`        │
 └──────────┘            └───────────────┬──────────────────────────────┘
        ▲                                │ 8kHz μ-law, 20ms frames, 2 legs
        │ pulls leads                    ▼
 ┌──────────┐            ┌─────────────────────────────────────────────┐
 │  Redis   │◀──────────▶│              Pipecat pipeline                │
 │ state /  │  per-call  │  Silero VAD → Saaras STT → context aggregator│
 │ cache /  │  state     │  → OpenAI (stream) → PRE-TTS FILTER → Bulbul │
 │ queue    │            │  ↑ barge-in / interruption lifecycle         │
 └────┬─────┘            └───────────────┬──────────────────────────────┘
      │ post-call job                    │ audio back to Twilio
      ▼                                  ▼
 ┌──────────┐                     lead hears Roma
 │ post-call│
 │  worker  │ ─▶ store recording ─▶ (later) CRM/calendar write
 └──────────┘
```

## Components (one responsibility each)
- **API adapters (`roma/api`)** — authenticate and translate HTTP/WebSocket traffic; business
  gates do not live in route handlers.
- **Application services (`roma/services`)** — coordinate outbound-call use cases and define
  transaction-sized operations.
- **Domain (`roma/domain`)** — provider-independent call rules, conversation state machine,
  appointment policy, safety rules, and spend policy.
- **Providers (`roma/providers`)** — Twilio and calendar integrations isolated from domain code.
- **Repositories (`roma/repositories`)** — Redis-backed state, status, lead, opener, and queue
  implementations.
- **Pipecat pipeline** — the real-time loop. Owns VAD, STT, LLM, filter, TTS, and the
  interruption lifecycle. One instance per active call.
- **Conversation controller** — the 7-phase state machine (`docs/03`). Plain Python. Decides
  *what* happens; the LLM decides *how to say it*.
- **Pre-TTS filter** — deterministic guardrail between LLM and TTS (`docs/04`). Gate-zero.
- **Redis** — three roles: (a) per-call state for resume-on-drop, (b) cache for fixed TTS
  phrases + filler tokens, (c) queue for post-call jobs.
- **Post-call worker** — consumes the queue: stores the recording; later, CRM/calendar write.

## Data flow, one turn
1. Twilio streams caller audio → Silero VAD marks speech.
2. Endpointing decides the turn ended (`docs/05`, 850ms default) → Saaras finalizes.
3. Controller updates call-state, picks the current phase's prompt fragment.
4. OpenAI streams a response; sentences flush as they complete.
5. **Filter inspects each sentence** before TTS. Clean → speak. Blocked → substitute a safe
   line. This step is non-skippable.
6. Bulbul speaks; audio returns to Twilio.
7. Barge-in at any point cancels 4–6 and flushes buffers (through the filter, not around it).

## Concurrency model (summary; full detail in `docs/08`)
- N concurrent calls = N async pipeline tasks in a process, scaled by process/replica.
- Nothing call-specific is global. All per-call state is keyed by Twilio Call SID in Redis.
- The post-call queue decouples slow work (storage, CRM) from the real-time loop.

## What is NOT in the hot path (by design)
- CRM / calendar writes → post-call queue.
- Recording persistence → post-call worker.
- Any network call that would add latency inside a turn → moved out or cached.

## Dependency direction

`api -> services -> domain` is the primary inward path. Redis and external APIs remain in
`repositories` and `providers`; the production composition root in `roma/main.py` assembles the
concrete backend. The project remains one deployable process family, not microservices.

The next architecture milestone is PostgreSQL-backed durable records and transaction-safe
appointment locking. See `docs/13-backend-roadmap.md`.
