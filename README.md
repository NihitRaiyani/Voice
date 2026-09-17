# Roma — Outbound Voice Agent for Weltec Institute

Roma is a production voice agent that calls warm leads (people who enquired about Weltec's
Digital Marketing course) and books a **specific day + time counselling visit**. It speaks
code-mix Gujarati / Hindi / English.

**This repo is currently a spec, not code.** Every `.md` here is a build contract. Build in
the order given in `docs/10-build-order.md`. Do not skip the gate-zero items in
`docs/07-security.md` and `docs/04-guardrails.md`.

## Scope (v1 — deliberately narrow)
- **In:** one outbound call flow that talks like a real counsellor and locks a visit slot.
- **In:** after the call ends, store the recording. Nothing fancy — save it, done.
- **Out (v1):** analytics dashboards, multi-course support, inbound calls, live CRM sync
  inside the call (post-call queue only).

## The one-line architecture
```
Vobiz ─▶ Pipecat (Silero VAD) ─▶ Saaras STT ─▶ OpenAI ─▶ pre-TTS filter ─▶ Bulbul TTS ─▶ Vobiz
                                         │
                         Redis (call-state + cache + queue)
                                         │
                   post-call worker ─▶ store recording
```

## Directory
```
roma/
├── README.md                  ← you are here
├── CLAUDE.md                  ← Claude Code working memory + skill wiring (read first)
├── docs/
│   ├── 01-architecture.md     ← components, data flow, concurrency model
│   ├── 02-pipeline.md         ← the per-turn Pipecat pipeline + latency budget
│   ├── 03-phase-machine.md    ← the 7-phase conversation state machine
│   ├── 04-guardrails.md       ← GATE-ZERO. pre-TTS money/guarantee filter
│   ├── 05-endpointing-vad.md  ← VAD, endpointing (850ms), barge-in
│   ├── 06-state-and-cache.md  ← Redis schemas, resume-on-drop, caching
│   ├── 07-security.md         ← GATE-ZERO. secrets, PII, consent, access
│   ├── 08-concurrency.md      ← multi-call model, async, worker pool
│   ├── 09-recording-storage.md← simple: store every recording post-call
│   ├── 10-build-order.md      ← the sequence, with gates
│   ├── 11-prompts.md          ← prompt structure, assembly order, caching contract
│   ├── session-continuity.md  ← how sessions hand off (SESSION/HANDOFF/LOG)
│   └── decisions.md           ← locked decisions + open items (D1, WER)
├── src/roma/prompts/          ← RUNTIME prompt text (loaded at startup, not docs)
│   ├── persona.md             ← always loaded
│   ├── hard_rules.md          ← always loaded
│   └── phases/p1_open.md … p7_close.md   ← exactly one loaded per turn
└── skills/
    ├── README.md              ← which skills, why, and the wiring rule
    └── sync-prompts.md        ← per-skill: paste-into-Claude-Code prompts
```

## Two things still open (do not block the build)
1. **Saaras WER on Gujarati** — must be measured on real audio (see `docs/decisions.md`).
2. **D1 (which certs are real)** — Weltec's answer. Until then, claim no certs beyond the
   Weltec certificate.

## Start the backend
Activate the project venv once per shell, then run everything with plain commands:
```bash
source .venv/bin/activate
uvicorn scripts.serve_media:create_app --factory --host 0.0.0.0 --port 8020
```
Serves the Vobiz `<Stream>` WebSocket `/ws` app on **:8020**. Run it from the repo root —
`scripts.serve_media` is imported relative to the working directory.

Point uvicorn at `scripts.serve_media:create_app`, **not** at
`roma.telephony.media:build_media_app`. The bare app factory skips `configure_logging()`,
which drops every `roma.telephony` INFO line — including the teardown
`media stream ended: ... inbound_frames=N` that proves audio actually arrived — and loses
secret redaction (docs/07). `create_app()` configures logging first, runs with
`auto_hang_up=False`, and mounts the read-only `/debug/*` routes
(`last-counter`, `last-transcript`, `last-spoken`, `last-phase`, `endpoint-timing`).

### Reachability: `PUBLIC_BASE_URL`, and the tunnel (dev only)

Vobiz reaches this process at TWO endpoints and neither can be `localhost`:

* `POST <PUBLIC_BASE_URL>/answer` — fetched when the callee picks up, to learn where to
  stream. (Twilio never did this: its TwiML travelled inline in the REST call, so the socket
  was the only inbound connection. The answer fetch is new with Vobiz.)
* `wss://<...>/ws` — the audio itself.

Both are derived from `PUBLIC_BASE_URL`, so the only requirement is that it names a
publicly-resolvable HTTPS host that terminates TLS in front of this app — the app itself
serves plaintext HTTP on `0.0.0.0:8020` with no TLS and no proxy-header handling.

Nothing in `src/` knows what supplies that host. **The tunnel is not an architectural
dependency** — there is zero Cloudflare code or configuration anywhere in the app. When the
permanent server is ready, `PUBLIC_BASE_URL` points at it and the Vobiz Application's Answer
URL points at the same host: **a configuration change, not a code change.**

**During the current integration-testing phase** the machine placing these calls is on a
private network with no public address, so a tunnel supplies a temporary one. It is scaffolding
for this phase only and comes out on deployment — nothing should be built assuming it:

```bash
cloudflared tunnel --url http://localhost:8020
```

## Place a live test call
```bash
python scripts/place_test_call.py +919327858018
```
Dials one outbound call into the running `/ws` spine (a one-shot CLI, not a server — no
uvicorn involved). The callee must be a **Verified Caller ID** in the Vobiz console
(trial account) — test handsets only, never a real lead. Uses `PUBLIC_BASE_URL` from
`.env`; override with `--base-url https://<sub>.trycloudflare.com`. Add `--ignore-window`
to dial outside the 09:00–21:00 IST calling window.

Three terminals, **dev**:

# 1 — backend
.venv/bin/uvicorn scripts.serve_media:create_app --factory --host 0.0.0.0 --port 8020

# 2 — tunnel (copy the https:// URL it prints; /answer and /ws are both derived from it)
cloudflared tunnel --url http://localhost:8020

# 3 — dial
.venv/bin/python scripts/place_test_call.py +919327858018 --base-url https://random-words-here.trycloudflare.com

In **production** step 2 disappears: uvicorn sits behind the host's TLS proxy,
`PUBLIC_BASE_URL=https://<vadodara-host>`, and `--base-url` is not passed at all.


