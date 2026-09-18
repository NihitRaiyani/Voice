# 02 — Pipeline & Latency

## Pipecat processor order (per call)
```
TwilioTransport(in)
  → SileroVAD
  → SaarasSTT (streaming, endpoint-on-final)
  → UserContextAggregator
  → ConversationController (7-phase; injects phase prompt + call-state)
  → OpenAILLM (streaming)
  → SentenceChunker (flush on sentence boundary)
  → PreTTSFilter        ← GATE-ZERO, sub-10ms, deterministic
  → BulbulTTS (persistent socket)
  → TwilioTransport(out)
```
Interruption/barge-in is a cross-cutting lifecycle Pipecat manages; the filter must sit
INSIDE that cancellation path (see `docs/04`).

## Latency budget (target: perceived 300–700ms; real floor ~700ms)
| Stage | Budget | Notes |
|---|---|---|
| Endpoint decision | ~850ms wait | not counted as "compute"; it's the turn-end wait |
| Twilio RTT | ~100ms | verify from Vadodara → Mumbai edge |
| Saaras STT finalize | ~200–300ms | on real 8kHz; measure |
| OpenAI TTFT | ~300–500ms | biggest lever; model choice matters |
| Filter | <10ms | deterministic, no model call |
| Bulbul first byte | ~150–250ms | persistent socket avoids reconnect cost |

**The budget only closes by hiding latency, not eliminating it:**
- **Stream + sentence-chunk:** flush OpenAI tokens to Bulbul on the first sentence boundary.
  Converts a 500ms completion into ~200ms perceived.
- **Filler token:** the instant endpointing fires, play a 200–300ms cached "achha…" / "haan
  ji…" while the LLM spins up. Perceived response ≈ 250ms even if the pipeline took ~900ms.
- **Persistent Bulbul socket:** no per-turn reconnect.

## Prompting for speech (not chat)
- No lists, no markdown, no "firstly/secondly." Numbers spelled as words (TTS reads digits
  unpredictably).
- Short sentences — they are the chunking boundaries. Long sentences = late first audio.
- One turn = one LLM call. Response length capped per phase (`docs/03`), enforced at the LLM,
  not by truncating TTS.

## Token & cost optimization (what applies to THIS project)

Roma calls the OpenAI *API* and reads a tiny static KB. So the relevant optimizations are the
ones that reduce what we send/receive per turn — not model-serving internals (those run on
OpenAI's side) and not database-query tricks (Roma has no in-call DB).

Applies — add these:
1. **Prompt caching (static prefix).** Structure each phase prompt so the fixed part — system
   instructions, the phase's rules, the static KB grounding — is the *prefix*, and only a small
   variable tail (call-state, last user turn) changes. OpenAI caches the prefix → lower input
   tokens and lower TTFT on every turn. Highest-ROI item for our stack.
2. **max_tokens per phase = the word cap (`docs/03`).** A hard output ceiling per phase caps
   output tokens AND latency together (fewer tokens = faster completion). Set it to match the
   phase's word cap, not a global default.
3. **Structured call-state instead of raw-transcript context (already in `docs/03`).** We pass
   ~10 state fields, not 40 turns of history. This is the single biggest token saver and it's
   already designed in — do not regress to stuffing the transcript into context.
4. **Fixed-phrase TTS + KB cache (already in `docs/06`).** Repeated lines (greeting, the four
   substitution lines, readback, EMI line) skip the LLM entirely → zero tokens on those turns.
5. **Model routing per phase.** Cheap model for simple phases (P1/P2 confirm/discover), stronger
   model only where phrasing quality matters (P3/P5/P6). Resolves the open model-choice item in
   `docs/decisions.md`. Measure real TTFT per model before locking.

Confidence thresholds (a form of validation, already partly designed):
- **Slot extraction:** `confidence < threshold → re-ask` (`docs/03`). Keep.
- **STT:** if Saaras returns low-confidence (likely on the quiet Gujarati far-end, per the audio
  analysis), re-ask rather than act on a garble. More important here than usual.

## NOT applicable to Roma — and why (do not re-add later)

Recorded so this list doesn't come back around. If a future session proposes one of these,
the burden is to show what changed — not to add it because it's on a generic checklist.

- **SQL generation / schema linking / SQL validation** — no in-call database. Roma reads a
  static KB, not a warehouse. (These belong to the *other* Weltec NL-to-SQL chatbot, not Roma.)
- **KV cache / FlashAttention / PagedAttention / Multi-Query Attention / speculative decoding /
  continuous & dynamic batching** — LLM-server internals. OpenAI runs these; we call the API and
  can't touch them. Only relevant if we ever self-host a model — the locked stack says we don't.
- **Speculative execution** (generating before the user finishes) — fights barge-in-heavy
  Hinglish endpointing and wastes tokens on interrupted turns. The filler token gets the same
  perceived-latency win without the complexity.
- **Semantic caching of LLM responses** — dangerous in a stateful phase machine (same words,
  different phase = wrong reply). The *fixed-phrase* cache (`docs/06`) is the safe form; keep
  that, not general response-level semantic cache.
- **Prompt compression / conversation-memory compression / context-window management** — a
  5-min call is ~40 turns, far under the window; the structured call-state already handles
  context. Compressing an already-lean voice prompt risks the register that makes Roma sound
  like Roma. Over-scoping for v1.
- **LLM-as-a-judge / hallucination metrics** — *eval* tools, not runtime. No judge-LLM call fits
  inside a 700ms budget. Use them OFFLINE in the replay harness (`docs/10` Step 7) to score
  filter leaks and hallucinations against the transcripts.
- **ReAct / Tree-of-Thoughts / Chain-of-Thought** — multi-step reasoning frameworks for
  tool-using agents. Roma does one thing per turn, no mid-turn tools. CoT adds latency + tokens
  for reasoning she doesn't need. Same reasoning that dropped LangGraph — if a phase seems to
  need CoT, the phase is doing too much.

## Acoustic front-end (noise suppression / AEC) — see `docs/05`

Noise suppression and acoustic echo cancellation matter here because the source audio showed a
continuous noise floor and a quiet far-end, and because Roma's own TTS can echo into the input
and false-trigger barge-in. Twilio handles some at the carrier level — verify what's covered
before adding our own. Detail lives in `docs/05` (VAD/endpointing), since it feeds that layer.

## Observability & cost monitoring

Two separate jobs, two mechanisms — don't reach for one tool to do both.
- **LLM cost/tokens:** log `response.usage` (input/cached/output tokens) per OpenAI call to a
  structured log, plus the OpenAI dashboard usage view. This is all that's needed to track the
  ₹100 mini cap — no third-party tracing layer. (LangSmith was considered and dropped: it's
  built for LangChain/LangGraph chains, which this stack deliberately doesn't use; wrapping raw
  OpenAI-SDK calls by hand isn't worth the instrumentation cost for what native usage logging
  already gives.)
- **Pipeline latency:** structured logs / Pipecat's own metrics for the acoustic stages
  (VAD → STT → filter → TTS). These aren't LLM calls, so LLM-tracing tools can't see them.

## Cost cap (testing phase)
- **Hard budget: OpenAI mini spend < ₹100 for the whole testing phase.** Revised down from
  ₹200 on 2026-07-26: ₹100 is what is actually left on the account, so it is the real
  ceiling. Enforce it with a **hard spend limit set in the OpenAI dashboard** — enforced by
  OpenAI, not discipline. `Settings.openai_budget_inr` mirrors it so the eval harness can
  refuse to start or stop mid-run, but that is a second belt, not the enforcement.
- Log per-call token counts from the first callable build so real ₹/call is known after ~10
  calls and can be extrapolated against the cap.
- The real test-run limiter is **Sarvam credits** (₹4.50/call on v2), not OpenAI. Skip live
  Twilio early (feed recorded audio through the pipeline) to stretch both.

## Verify before wiring
- Pipecat's current turn-taking / smart-endpointing API (moves fast — use context7).
- OpenAI model per turn: quality vs TTFT. Measure real TTFT before committing.
- OpenAI prompt-caching mechanics (prefix rules, minimum cacheable length) — confirm current
  behavior with context7 before structuring the prompts around it.