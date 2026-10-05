#!/usr/bin/env bash
# Start or stop local PostgreSQL and Redis for the Level 1 native foundation.
# This script never installs packages and never writes secrets into the repository.

set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
STATE_DIR="$ROOT/var/dev-services"
PGDATA="${ROMA_DEV_PGDATA:-$STATE_DIR/postgres}"
REDIS_DIR="${ROMA_DEV_REDIS_DIR:-$STATE_DIR/redis}"
PGHOST="${ROMA_DEV_PGHOST:-127.0.0.1}"
PGPORT="${ROMA_DEV_PGPORT:-54329}"
PGUSER="${ROMA_DEV_PGUSER:-roma}"
PGDATABASE="${ROMA_DEV_PGDATABASE:-roma}"
PGPASSWORD_VALUE="${ROMA_DEV_PGPASSWORD:-roma}"
REDIS_HOST="${ROMA_DEV_REDIS_HOST:-127.0.0.1}"
REDIS_PORT="${ROMA_DEV_REDIS_PORT:-6380}"

[[ "$PGUSER" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || { printf 'invalid ROMA_DEV_PGUSER\n' >&2; exit 1; }
[[ "$PGDATABASE" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || { printf 'invalid ROMA_DEV_PGDATABASE\n' >&2; exit 1; }

log() { printf '\033[36m>>\033[0m %s\n' "$*"; }
ok() { printf '\033[32m ✓\033[0m %s\n' "$*"; }
die() { printf '\033[31m ✗\033[0m %s\n' "$*" >&2; exit 1; }

find_cmd() {
  local name="$1"
  shift || true
  if command -v "$name" >/dev/null 2>&1; then
    command -v "$name"
    return 0
  fi
  local candidate
  for candidate in \
    "/opt/homebrew/bin/$name" \
    "/usr/local/bin/$name" \
    "/Library/PostgreSQL/18/bin/$name" \
    "/Library/PostgreSQL/17/bin/$name" \
    "/Library/PostgreSQL/16/bin/$name" \
    "/usr/lib/postgresql/18/bin/$name" \
    "/usr/lib/postgresql/17/bin/$name" \
    "/usr/lib/postgresql/16/bin/$name" \
    "$@"; do
    if [[ -x "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

pg_ctl_bin="$(find_cmd pg_ctl || true)"
initdb_bin="$(find_cmd initdb || true)"
psql_bin="$(find_cmd psql || true)"
createdb_bin="$(find_cmd createdb || true)"
redis_server_bin="$(find_cmd redis-server || true)"
redis_cli_bin="$(find_cmd redis-cli || true)"

require_postgres_bins() {
  [[ -n "$pg_ctl_bin" ]] || die "pg_ctl is not installed or not on a known path"
  [[ -n "$initdb_bin" ]] || die "initdb is not installed or not on a known path"
  [[ -n "$psql_bin" ]] || die "psql is not installed or not on a known path"
  [[ -n "$createdb_bin" ]] || die "createdb is not installed or not on a known path"
}

require_redis_bins() {
  [[ -n "$redis_server_bin" ]] || die "redis-server is not installed or not on a known path"
  [[ -n "$redis_cli_bin" ]] || die "redis-cli is not installed or not on a known path"
}

start_postgres() {
  require_postgres_bins
  mkdir -p "$STATE_DIR"
  if [[ ! -s "$PGDATA/PG_VERSION" ]]; then
    log "initializing local PostgreSQL cluster at var/dev-services/postgres"
    mkdir -p "$PGDATA"
    "$initdb_bin" -D "$PGDATA" -A trust -U postgres >/dev/null
    {
      printf "listen_addresses = '%s'\n" "$PGHOST"
      printf "port = %s\n" "$PGPORT"
      printf "unix_socket_directories = '%s'\n" "$PGDATA"
    } >> "$PGDATA/postgresql.conf"
  fi

  if "$pg_ctl_bin" -D "$PGDATA" status >/dev/null 2>&1; then
    ok "postgres already running on $PGHOST:$PGPORT"
  else
    log "starting local PostgreSQL on $PGHOST:$PGPORT"
    "$pg_ctl_bin" -D "$PGDATA" -l "$STATE_DIR/postgres.log" start -w >/dev/null
    ok "postgres"
  fi

  if ! "$psql_bin" "postgresql://postgres@$PGHOST:$PGPORT/postgres" -tAc \
    "SELECT 1 FROM pg_roles WHERE rolname = '$PGUSER'" | grep -qx 1; then
    "$psql_bin" "postgresql://postgres@$PGHOST:$PGPORT/postgres" -v ON_ERROR_STOP=1 -c \
      "CREATE ROLE $PGUSER LOGIN PASSWORD '$PGPASSWORD_VALUE'" >/dev/null
  fi
  "$createdb_bin" --host "$PGHOST" --port "$PGPORT" --username postgres \
    --owner "$PGUSER" "$PGDATABASE" >/dev/null 2>&1 || true
}

start_redis() {
  require_redis_bins
  mkdir -p "$REDIS_DIR"
  if "$redis_cli_bin" -h "$REDIS_HOST" -p "$REDIS_PORT" ping >/dev/null 2>&1; then
    ok "redis already running on $REDIS_HOST:$REDIS_PORT"
    return
  fi
  log "starting local Redis on $REDIS_HOST:$REDIS_PORT"
  "$redis_server_bin" --daemonize yes --bind "$REDIS_HOST" --port "$REDIS_PORT" \
    --dir "$REDIS_DIR" --dbfilename dump.rdb --pidfile "$STATE_DIR/redis.pid" \
    --logfile "$STATE_DIR/redis.log" >/dev/null
  sleep 1
  "$redis_cli_bin" -h "$REDIS_HOST" -p "$REDIS_PORT" ping >/dev/null || die "redis started but is not answering"
  ok "redis"
}

stop_postgres() {
  if [[ -n "$pg_ctl_bin" && -s "$PGDATA/PG_VERSION" ]]; then
    "$pg_ctl_bin" -D "$PGDATA" stop -m fast >/dev/null 2>&1 || true
  fi
}

stop_redis() {
  if [[ -n "$redis_cli_bin" ]]; then
    "$redis_cli_bin" -h "$REDIS_HOST" -p "$REDIS_PORT" shutdown >/dev/null 2>&1 || true
  fi
}

case "${1:-up}" in
  up|start)
    start_postgres
    start_redis
    printf '\nDATABASE_URL=postgresql+asyncpg://%s:%s@%s:%s/%s\n' \
      "$PGUSER" "$PGPASSWORD_VALUE" "$PGHOST" "$PGPORT" "$PGDATABASE"
    printf 'REDIS_URL=redis://%s:%s/0\n' "$REDIS_HOST" "$REDIS_PORT"
    ;;
  down|stop)
    log "stopping local development services"
    stop_redis
    stop_postgres
    ok "stopped"
    ;;
  *)
    die "usage: scripts/dev_services.sh [up|down]"
    ;;
esac
