# 13 — Web UI: place an outbound call from a browser

A one-input, one-button page for placing a test call, so it stops being a shell command with
four flags. Deliberately minimal: no dashboard, no call history, no styling framework.

**Local only.** Production deploy is still UNDECIDED (see `decisions.md`). It has a bearer
token, but that token is inlined into the browser bundle and is not real authentication —
read *Security* below before running this anywhere but a laptop.

## Shape

    browser :3030  ──HTTP/JSON──▶  FastAPI :8020  ──▶  Vobiz
     (Vite + React)                (the app that already
                                    serves /answer and /ws)

There is **no Node backend**. React talks to FastAPI directly; an Express layer would only
proxy JSON to a service we already run, and would add a second place for the dial gate to be
bypassed. Recorded in `decisions.md`.

## Endpoints

Both are mounted by `roma.telephony.webapi.mount_web_api`, called from
`scripts/serve_media.py::create_app` — **not** by `build_media_app`. The offline test suite
constructs that factory directly, and a surface that rings real phones does not belong in the
object 1300 tests build by default.

### `POST /api/call`

```json
{ "to_number": "+919876543210" }
```

Order of operations, and the order matters:

0. **Bearer token** (`Authorization: Bearer <API_TOKEN>`). `401` on mismatch, `503` when
   `API_TOKEN` is unset — it **fails closed**, because an unconfigured lock must not be
   indistinguishable from "needs no lock".
1. **Indian mobile shape** (`dialer.dnd.is_indian_mobile`): `+91` then ten digits opening
   `6-9`. `400` on failure, before any ledger read or Redis round trip. **Roma is not a
   general dialer** — a `+1` number is a mistake, and a 400 is cheaper than an international
   leg on the bill. Spaces are not E.164 either.
2. **Gate 0** (`dialer.precall.precall_check`): calling window → DNC → budget, in that order,
   with a DENYLIST built from `DND_NUMBERS`. Never from the request body — only `to_number`
   is read from the payload, so no request can widen a gate.
2b. **Hourly cap** (`MAX_CALLS_PER_HOUR`), after the gates so a refused call costs nothing
   and before dialing so it actually stops one. `429` when spent.
3. **Dial** via `dialer.trigger.trigger_outbound_call` — the existing outbound path, unchanged.
   It mints the per-call lead token and builds `<PUBLIC_BASE_URL>/answer?lead=<token>`; this
   endpoint does not rebuild either.
4. **Record** the call as `dialing` in `dialer.callstatus`.

| response | meaning |
|---|---|
| `200 {"request_uuid", "status": "dialing"}` | the call is fired |
| `400 {"detail": "..."}` | malformed number or body |
| `409 {"detail": {"status": "blocked", "reason": "..."}}` | a gate refused it |
| `401 {"detail": "..."}` | bad or missing bearer token |
| `429 {"detail": {"reason": "cap:hourly (N/hour)"}}` | the hour's dial budget is spent |
| `503 {"detail": "API_TOKEN is not configured..."}` | the lock is unset — endpoint disabled |
| `503 {"detail": "cannot dial: ..."}` | no Vobiz account credentials |

Block reasons pass through from `precall_check` verbatim and are the useful part of the
answer — the UI renders them as-is rather than prettifying them:

- `block:window` — outside the 09:00–21:00 IST calling window
- `block:dnd` — the number IS on `DND_NUMBERS`
- `block:budget` — `OPENAI_BUDGET_INR` is spent
- `precall-error:<Type>` — the gate itself failed, which blocks (never allows)

**Never logged or returned:** the lead token (an authority) and the callee's number (the
lead's PII, docs/07). The `request_uuid` is the only one of the three that may be.

### `GET /api/call/{request_uuid}`

```json
{ "request_uuid": "...", "status": "dialing", "reason": "" }
```

`404` when unknown or expired.

| status | written by |
|---|---|
| `dialing` | `POST /api/call`, at dial |
| `connected` | `/answer` — Vobiz only fetches it once the callee picks up |
| `ended` | the `/ws` teardown, before the call is popped from `app.state.calls` |
| `no_answer` | **nobody — it is derived from the record's age** |

`no_answer` deserves its own note. Vobiz is sent no status callback (`create_call` posts four
fields: `from`, `to`, `answer_url`, `answer_method`), so when a callee simply does not pick
up, **nothing in this system ever hears about it**: `/answer` is never fetched and no socket
opens. A UI waiting for a writer would sit on `dialing` for ever. So a record still `dialing`
after `NO_ANSWER_AFTER_SECS` (60) reads as `no_answer`. Only `dialing` ages out — a long call
that was answered is never relabelled.

Both status writes are **fail-open**: a status-store failure logs a warning and never touches
the call. The UI showing a stale label is cosmetic; an exception on the media path is not.

## Ports and environment

| | port | notes |
|---|---|---|
| frontend (Vite) | **3030** | `--strictPort`, so a clash refuses to start rather than silently moving to an origin CORS does not allow |
| backend (uvicorn) | **8020** | unchanged |

Backend (`.env`):

- `API_TOKEN` — **required**, or the endpoint returns 503. Generate with
  `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
- `WEB_ORIGIN` — the ONE origin CORS admits. Default `http://localhost:3030`. **Never `*`.**
- `DND_NUMBERS` — comma-separated denylist for Gate 0. **Empty blocks nothing** (see below).
- `MAX_CALLS_PER_HOUR` — default 20.
- `BIND_HOST` — default `127.0.0.1`. Going wide is a deliberate act.
- `CALL_STATUS_TTL_SECS` — how long a status row outlives its call. Default 3600.

Frontend (`web/.env`, see `web/.env.example`):

- `VITE_API_BASE` — e.g. `http://localhost:8020`. Never hardcoded in source, so moving to a
  real host later is a config change rather than a rewrite.
- `VITE_API_TOKEN` — must match the backend's `API_TOKEN`. **Not a secret** — see Security.

## Running it

```bash
# backend — 127.0.0.1 is the default now, not something to remember
uv run uvicorn scripts.serve_media:create_app --factory --host 127.0.0.1 --port 8020

# frontend
cd web && npm install && npm run dev
```

Set `API_TOKEN` in `.env` and the same value as `VITE_API_TOKEN` in `web/.env`. **No config
edit is needed per test number any more** — that was the point of the change.

## Security

**This endpoint rings real phones and spends the OpenAI budget.** The consent allowlist that
used to bound it was removed on 2026-08-04 (see `decisions.md`) because it was a testing
safeguard with real friction and no production value — Weltec's enquiry list is the actual
consent basis. **Gate 0 is therefore allow-by-default on this path**: any valid Indian mobile
dials unless it is on `DND_NUMBERS`, and an empty denylist blocks nothing. That is stated
plainly here rather than left to be discovered.

What bounds it instead, in order:

- **Bearer auth, failing closed.** No `API_TOKEN` ⇒ 503. Compared with `secrets.compare_digest`.
- **Indian-mobile-only validation.** Not a general dialer.
- **The calling window and the spend cap**, unchanged.
- **An hourly dial cap**, which catches what the spend cap cannot see coming: a stuck retry
  loop costs 20 calls, not the budget, and fails in minutes rather than after the money.
- **`BIND_HOST=127.0.0.1` by default.** Binding wide is now an explicit config act.
- **Only `to_number` is read from the body.** No request can widen a gate — there is no code
  path from the payload to the registry, the window or the cap. Pinned by
  `test_the_request_body_cannot_widen_or_bypass_any_gate`.

### What the token is not

The browser sends it from `VITE_API_TOKEN`, and **Vite inlines that into the JS bundle**. It
is therefore not secret from anyone who can load the page. It guards the *network* boundary —
another machine reaching :8020 — which is the threat it was added for. It is **not** real
authentication, and CORS is not either: CORS constrains browsers, not `curl`.

**Before this is reachable off localhost it needs a session-based login.** Production deploy
remains UNDECIDED (`decisions.md`).
