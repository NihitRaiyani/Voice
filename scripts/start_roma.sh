#!/usr/bin/env bash
#
# Bring the whole live-call stack up with one command, and make the tunnel URL nobody's
# problem.
#
#   ./scripts/start_roma.sh          # start everything, print the UI URL
#   ./scripts/start_roma.sh --stop   # stop everything
#
# ## Why this exists
#
# The stack is four processes (redis, cloudflared, uvicorn, vite) and exactly one piece of
# state that has to travel between two of them: the ephemeral hostname `cloudflared` mints
# at startup, which `PUBLIC_BASE_URL` must carry to uvicorn so `/answer` can hand the
# carrier a `wss://` URL it can actually dial.
#
# That handoff was manual, and it broke the dial button twice in two days. A quick tunnel
# is not durable — the process survives, its hostname leaves DNS, and nothing in the system
# notices because nothing re-reads it. `.env` then points at a host that no longer exists,
# Vobiz 400s on the answer URL, and the operator sees `carrier refused`, which sends them
# looking at the carrier. On 2026-08-05 the tunnel had been retrying a dead control stream
# for five hours while `.env` still named it.
#
# So the handoff is code now. The script mints the tunnel, waits for the hostname, writes it
# into `.env` itself, and REFUSES TO FINISH unless the carrier's round trip actually works.
# The failure mode it replaces is a stack that looks healthy and cannot place a call.

set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
RUN_DIR="$ROOT/var/run"
mkdir -p "$RUN_DIR"

TUNNEL_LOG="$RUN_DIR/cloudflared.log"
SERVER_LOG="$RUN_DIR/uvicorn.log"
WEB_LOG="$RUN_DIR/vite.log"

log()  { printf '\033[36m>>\033[0m %s\n' "$*"; }
ok()   { printf '\033[32m ✓\033[0m %s\n' "$*"; }
die()  { printf '\033[31m ✗\033[0m %s\n' "$*" >&2; exit 1; }

stop_all() {
  log "stopping Roma"
  for name in "cloudflared tunnel" "uvicorn scripts.serve_media" "vite --port 3030"; do
    pkill -f "$name" 2>/dev/null && ok "stopped: $name" || true
  done
}

if [[ "${1:-}" == "--stop" ]]; then
  stop_all
  exit 0
fi

# --- 0. preconditions --------------------------------------------------------------------
[[ -f .env ]] || die ".env is missing. Copy .env.example and fill it in."
command -v cloudflared >/dev/null || die "cloudflared is not installed (brew install cloudflared)"
[[ -x .venv/bin/uvicorn ]] || die ".venv is missing. Run: uv sync"

if ! redis-cli ping >/dev/null 2>&1; then
  log "redis is not answering; starting it"
  # Backgrounded rather than `brew services`, so this script owns nothing it cannot also
  # stop, and a machine without Homebrew services still works.
  #
  # `--dir` matters: without it redis dumps its RDB into whatever directory it started in,
  # and on 2026-08-08 that was the repo root — a dump.rdb full of roma:lead PII, tracked in
  # git. `var/` is gitignored and already the PII home (docs/07).
  mkdir -p "$ROOT/var"
  redis-server --daemonize yes --dir "$ROOT/var" >/dev/null 2>&1 || die "could not start redis"
  sleep 1
  redis-cli ping >/dev/null 2>&1 || die "redis started but is not answering"
fi
ok "redis"

# API_TOKEN gates the dial endpoint and FAILS CLOSED (503) when unset, so catch it here
# rather than letting the operator find out by pressing the button.
grep -qE '^API_TOKEN=.+' .env || die "API_TOKEN is not set in .env — the dial endpoint would return 503"
if [[ -f web/.env ]]; then
  back="$(grep -E '^API_TOKEN=' .env | cut -d= -f2-)"
  front="$(grep -E '^VITE_API_TOKEN=' web/.env | cut -d= -f2- || true)"
  [[ "$back" == "$front" ]] || die "API_TOKEN in .env and VITE_API_TOKEN in web/.env differ — every dial would be a 401"
fi
ok "api token present and matching"

# --- 1. clear anything already running ---------------------------------------------------
stop_all
sleep 1

# --- 2. the tunnel, its hostname, and an edge on the right continent ----------------------
#
# Both calls in this hop are India-to-India: Vobiz's Mumbai edge to a laptop in Vadodara. The
# Cloudflare edge sits in the middle of every single audio frame, so WHICH edge is picked is
# a latency decision, not a detail — a European one adds a continent of round trip to a
# conversation already fighting for sub-1.5s turns.
#
# `--edge-ip-version 4` helps but does not decide it, so the script re-draws until the edge
# is in India rather than hoping. Observed on 2026-08-05: amd01, then del03/04/05.
#
# READ THE CODE CAREFULLY BEFORE ADDING ONE. `amd` is AHMEDABAD — a hundred kilometres from
# Vadodara and the best draw available. AMSTERDAM is `ams`. Those two were confused once
# already while writing this script, and the "fix" was to reject the best possible edge.
INDIAN_EDGE='^(bom|amd|del|maa|blr|hyd|ccu|nag|kno)[0-9]'  # Cloudflare's India PoPs
BASE_URL=""
EDGE=""
for attempt in 1 2 3 4 5; do
  log "starting cloudflared (attempt $attempt)"
  : > "$TUNNEL_LOG"
  nohup cloudflared tunnel --protocol quic --edge-ip-version 4 \
    --url http://127.0.0.1:8020 >>"$TUNNEL_LOG" 2>&1 </dev/null &

  BASE_URL=""
  for _ in $(seq 1 60); do
    BASE_URL="$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$TUNNEL_LOG" | head -1 || true)"
    [[ -n "$BASE_URL" && -n "$(grep -oE 'location=[a-z0-9]+' "$TUNNEL_LOG" | head -1)" ]] && break
    sleep 0.5
  done
  [[ -n "$BASE_URL" ]] || die "cloudflared did not print a hostname in 30s — see $TUNNEL_LOG"

  EDGE="$(grep -oE 'location=[a-z0-9]+' "$TUNNEL_LOG" | head -1 | cut -d= -f2)"
  if [[ "$EDGE" =~ $INDIAN_EDGE ]]; then
    ok "tunnel: $BASE_URL  (edge $EDGE)"
    break
  fi
  printf '\033[33m ! \033[0m edge %s is not in India — re-drawing\n' "${EDGE:-unknown}"
  pkill -f "cloudflared tunnel" 2>/dev/null || true
  sleep 2
  BASE_URL=""
done

if [[ -z "$BASE_URL" ]]; then
  # Five non-Indian draws is unusual enough to be a network problem, not bad luck. Take the
  # last one rather than refusing to start — a laggy call still beats no call — but say so.
  log "could not draw an Indian edge in 5 tries; starting anyway"
  : > "$TUNNEL_LOG"
  nohup cloudflared tunnel --protocol quic --edge-ip-version 4 \
    --url http://127.0.0.1:8020 >>"$TUNNEL_LOG" 2>&1 </dev/null &
  for _ in $(seq 1 60); do
    BASE_URL="$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$TUNNEL_LOG" | head -1 || true)"
    [[ -n "$BASE_URL" ]] && break
    sleep 0.5
  done
  [[ -n "$BASE_URL" ]] || die "cloudflared did not start — see $TUNNEL_LOG"
  printf '\033[33m ! \033[0m EXPECT AUDIO LAG: the edge is outside India\n'
fi

# Write it into .env, replacing the line rather than appending: a second PUBLIC_BASE_URL
# would be read instead of the first and we would be back to two sources disagreeing, which
# is the whole failure this script exists to end.
python3 - "$BASE_URL" <<'PY'
import pathlib, re, sys
base = sys.argv[1]
p = pathlib.Path(".env")
text = p.read_text()
line = f"PUBLIC_BASE_URL={base}"
text, n = re.subn(r"(?m)^PUBLIC_BASE_URL=.*$", line, text)
if n == 0:
    text = text.rstrip("\n") + f"\n{line}\n"
p.write_text(text)
PY
ok "PUBLIC_BASE_URL written to .env"

# --- 3. the media server -----------------------------------------------------------------
# No inline PUBLIC_BASE_URL override. The server reads .env, which this script just wrote,
# so the two cannot drift — an inline value that disagreed with .env is how the first of the
# two outages happened.
log "starting the media server on 127.0.0.1:8020"
nohup .venv/bin/uvicorn scripts.serve_media:create_app --factory \
  --host 127.0.0.1 --port 8020 >>"$SERVER_LOG" 2>&1 </dev/null &

for _ in $(seq 1 60); do
  curl -fsS --max-time 2 http://127.0.0.1:8020/health >/dev/null 2>&1 && break
  sleep 0.5
done
curl -fsS --max-time 2 http://127.0.0.1:8020/health >/dev/null 2>&1 \
  || die "the server did not come up — see $SERVER_LOG"
ok "server up locally"

# --- 4. the check that actually matters --------------------------------------------------
# Everything above can succeed while the carrier still cannot reach us. This is the only
# step that tests the full path Vobiz will take: DNS, the edge, the tunnel, this server.
# A quick tunnel's hostname takes a few seconds to propagate, hence the retries.
log "verifying the carrier can reach us"
REACHED=""
for _ in $(seq 1 30); do
  code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 8 "$BASE_URL/health" 2>/dev/null || true)"
  if [[ "$code" == "200" ]]; then REACHED="yes"; break; fi
  sleep 1
done
[[ -n "$REACHED" ]] || die "$BASE_URL/health is not answering 200 — the carrier could not reach us either, so a call would ring and connect to nothing. See $TUNNEL_LOG"
ok "carrier path verified end to end"

# --- 5. the browser UI -------------------------------------------------------------------
if [[ -d web/node_modules ]]; then
  log "starting the web UI on 3030"
  (cd web && nohup npm run dev >>"$WEB_LOG" 2>&1 </dev/null &)
  for _ in $(seq 1 40); do
    curl -fsS --max-time 2 http://localhost:3030 >/dev/null 2>&1 && break
    sleep 0.5
  done
  curl -fsS --max-time 2 http://localhost:3030 >/dev/null 2>&1 \
    && ok "web UI up" || printf '\033[33m ! \033[0m web UI did not answer — see %s\n' "$WEB_LOG"
else
  printf '\033[33m ! \033[0m web/node_modules missing — run: cd web && npm install\n'
fi

printf '\n\033[32mRoma is up.\033[0m  Open \033[1mhttp://localhost:3030\033[0m and dial.\n'
printf '  tunnel  %s\n  logs    %s\n  stop    ./scripts/start_roma.sh --stop\n\n' "$BASE_URL" "$RUN_DIR"
