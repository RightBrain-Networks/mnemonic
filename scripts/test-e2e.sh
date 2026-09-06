#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd -- "$script_dir/.." && pwd)
compose_file="$repo_root/compose.e2e.yaml"

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

cleanup() {
  local status=$?
  trap - EXIT INT TERM
  docker compose -p "$MNEMONIC_E2E_COMPOSE_PROJECT" -f "$compose_file" down -v --remove-orphans >/dev/null 2>&1 || true
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

docker compose -p "$MNEMONIC_E2E_COMPOSE_PROJECT" -f "$compose_file" up -d --build --wait
(
  cd -- "$repo_root/frontend"
  npm run test:e2e -- "$@"
)
