#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd -- "$script_dir/.." && pwd)
compose_file="$repo_root/compose.e2e.yaml"
run_browser=true
if [[ "${1:-}" == "--backup-service-only" ]]; then
  run_browser=false
  shift
fi

if [[ ! -f "$compose_file" || -L "$compose_file" ]]; then
  echo "Expected a regular compose.e2e.yaml at the repository root." >&2
  exit 2
fi

project_suffix="$(date +%s)-$$-$RANDOM"
export MNEMONIC_E2E_COMPOSE_PROJECT="mnemonic-e2e-$project_suffix"
if [[ ! "$MNEMONIC_E2E_COMPOSE_PROJECT" =~ ^mnemonic-e2e-[a-z0-9-]+$ ]]; then
  echo "Refusing to manage an unexpected Compose project name." >&2
  exit 2
fi

repo_lock_key=$(printf '%s' "$repo_root" | sha256sum | cut -c1-16)
lock_file="${TMPDIR:-/tmp}/mnemonic-e2e-$repo_lock_key.lock"
exec 9>"$lock_file"
if ! flock -n 9; then
  echo "Another Mnemonic browser stack is already using this checkout." >&2
  exit 2
fi

port_is_free() {
  python3 - "$1" <<'PY'
import socket
import sys

port = int(sys.argv[1])
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    sock.bind(("127.0.0.1", port))
PY
}

choose_port() {
  local requested="$1"
  local candidate="$2"
  if [[ -n "$requested" ]]; then
    if [[ ! "$requested" =~ ^[0-9]+$ ]] || (( requested < 1024 || requested > 65535 )); then
      echo "E2E ports must be integers from 1024 through 65535." >&2
      return 2
    fi
    if ! port_is_free "$requested" 2>/dev/null; then
      echo "Requested E2E port $requested is already in use." >&2
      return 2
    fi
    printf '%s\n' "$requested"
    return
  fi
  while (( candidate <= 65535 )); do
    if port_is_free "$candidate" 2>/dev/null; then
      printf '%s\n' "$candidate"
      return
    fi
    ((candidate += 1))
  done
  echo "Could not find an available loopback port." >&2
  return 2
}

web_start=$((32000 + RANDOM % 5000))
api_start=$((37000 + RANDOM % 5000))
MNEMONIC_E2E_WEB_PORT=$(choose_port "${MNEMONIC_E2E_WEB_PORT:-}" "$web_start")
MNEMONIC_E2E_API_PORT=$(choose_port "${MNEMONIC_E2E_API_PORT:-}" "$api_start")
export MNEMONIC_E2E_WEB_PORT MNEMONIC_E2E_API_PORT
if [[ "$MNEMONIC_E2E_WEB_PORT" == "$MNEMONIC_E2E_API_PORT" ]]; then
  echo "The E2E web and API ports must be different." >&2
  exit 2
fi

export MNEMONIC_E2E_WEB_URL="http://127.0.0.1:$MNEMONIC_E2E_WEB_PORT"
export MNEMONIC_E2E_API_URL="http://127.0.0.1:$MNEMONIC_E2E_API_PORT"
export MNEMONIC_DASHBOARD_ORIGINS="$MNEMONIC_E2E_WEB_URL"
export MNEMONIC_E2E_API_KEY
MNEMONIC_E2E_API_KEY=$(openssl rand -hex 32)
export MNEMONIC_E2E_BACKUP_TOKEN
MNEMONIC_E2E_BACKUP_TOKEN=$(openssl rand -hex 32)

# A fresh host bind for each acceptance run; never use production artifacts.
MNEMONIC_E2E_ARTIFACT_DIR=$(mktemp -d /tmp/mnemonic-e2e-artifacts.XXXXXXXX)
MNEMONIC_E2E_BACKUP_DIR=$(mktemp -d /tmp/mnemonic-e2e-backups.XXXXXXXX)
MNEMONIC_E2E_TRANSCRIPT_INDEX_DIR=$(mktemp -d /tmp/mnemonic-e2e-index.XXXXXXXX)
export MNEMONIC_E2E_ARTIFACT_DIR MNEMONIC_E2E_BACKUP_DIR MNEMONIC_E2E_TRANSCRIPT_INDEX_DIR

clean_disposable_directory() {
  local directory="$1"
  if [[ ! "$directory" =~ ^/tmp/mnemonic-e2e-(artifacts|backups|index)\.[[:alnum:]]{8}$ ]] \
    || [[ ! -d "$directory" || -L "$directory" ]]; then
    echo "Refusing to clean an unexpected E2E storage directory." >&2
    return 2
  fi
  docker run --rm --user 0 --mount "type=bind,source=$directory,target=/storage" \
    postgres:17-alpine sh -c 'find /storage -mindepth 1 -delete; chown "$1:$2" /storage' \
    sh "$(id -u)" "$(id -g)" >/dev/null 2>&1 || true
  rmdir -- "$directory" || true
}

cleanup() {
  local status=$?
  trap - EXIT INT TERM
  if (( status != 0 )); then
    docker compose -p "$MNEMONIC_E2E_COMPOSE_PROJECT" -f "$compose_file" logs --no-color --tail 200 api web backup || true
  fi
  docker compose -p "$MNEMONIC_E2E_COMPOSE_PROJECT" -f "$compose_file" down -v --remove-orphans >/dev/null 2>&1 || true
  clean_disposable_directory "$MNEMONIC_E2E_ARTIFACT_DIR" || true
  clean_disposable_directory "$MNEMONIC_E2E_BACKUP_DIR" || true
  clean_disposable_directory "$MNEMONIC_E2E_TRANSCRIPT_INDEX_DIR" || true
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

docker run --rm --user 0 \
  --mount "type=bind,source=$MNEMONIC_E2E_ARTIFACT_DIR,target=/artifacts" \
  --mount "type=bind,source=$MNEMONIC_E2E_BACKUP_DIR,target=/backups" \
  --mount "type=bind,source=$MNEMONIC_E2E_TRANSCRIPT_INDEX_DIR,target=/index" \
  postgres:17-alpine chown 10001:10001 /artifacts /backups /index

services=(api backup tika)
if [[ "$run_browser" == true ]]; then
  services+=(web)
fi
if ! docker compose -p "$MNEMONIC_E2E_COMPOSE_PROJECT" -f "$compose_file" up -d --build --wait "${services[@]}"; then
  docker compose -p "$MNEMONIC_E2E_COMPOSE_PROJECT" -f "$compose_file" logs --no-color api web backup
  exit 1
fi
if [[ "$run_browser" == true ]]; then
  (
    cd -- "$repo_root/frontend"
    npm run test:e2e -- "$@"
  )
fi
docker compose -p "$MNEMONIC_E2E_COMPOSE_PROJECT" -f "$compose_file" exec -T --user 0 \
  -e "MNEMONIC_TEST_API_KEY=$MNEMONIC_E2E_API_KEY" \
  -e "MNEMONIC_E2E_COMPOSE_PROJECT=$MNEMONIC_E2E_COMPOSE_PROJECT" \
  backup python - < "$repo_root/scripts/test_project_backups.py"
