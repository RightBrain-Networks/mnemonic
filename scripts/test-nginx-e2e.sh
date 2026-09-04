#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd -- "$script_dir/.." && pwd)
base_compose="$repo_root/compose.e2e.yaml"
nginx_compose="$repo_root/compose.nginx-e2e.yaml"

for compose_file in "$base_compose" "$nginx_compose"; do
  if [[ ! -f "$compose_file" || -L "$compose_file" ]]; then
    echo "Expected a regular Compose file: $compose_file" >&2
    exit 2
  fi
done

project_suffix="$(date +%s)-$$-$RANDOM"
export MNEMONIC_E2E_COMPOSE_PROJECT="mnemonic-nginx-e2e-$project_suffix"
if [[ ! "$MNEMONIC_E2E_COMPOSE_PROJECT" =~ ^mnemonic-nginx-e2e-[a-z0-9-]+$ ]]; then
  echo "Refusing to manage an unexpected Compose project name." >&2
  exit 2
fi

choose_port() {
  python3 - <<'PY'
import socket

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PY
}

export MNEMONIC_E2E_WEB_PORT
export MNEMONIC_E2E_API_PORT
export MNEMONIC_NGINX_E2E_PORT
MNEMONIC_E2E_WEB_PORT=$(choose_port)
MNEMONIC_E2E_API_PORT=$(choose_port)
MNEMONIC_NGINX_E2E_PORT=$(choose_port)
if [[ "$MNEMONIC_E2E_WEB_PORT" == "$MNEMONIC_E2E_API_PORT" ||
      "$MNEMONIC_E2E_WEB_PORT" == "$MNEMONIC_NGINX_E2E_PORT" ||
      "$MNEMONIC_E2E_API_PORT" == "$MNEMONIC_NGINX_E2E_PORT" ]]; then
  echo "Disposable test ports unexpectedly collided; retry the runner." >&2
  exit 2
fi

export MNEMONIC_NGINX_E2E_URL="http://127.0.0.1:$MNEMONIC_NGINX_E2E_PORT"
export MNEMONIC_DASHBOARD_ORIGINS="$MNEMONIC_NGINX_E2E_URL"
export MNEMONIC_E2E_API_KEY
MNEMONIC_E2E_API_KEY=$(openssl rand -hex 32)

test_tmp=$(mktemp -d)
cleanup() {
  local status=$?
  trap - EXIT INT TERM
  docker compose -p "$MNEMONIC_E2E_COMPOSE_PROJECT" -f "$base_compose" -f "$nginx_compose" \
    down -v --remove-orphans --rmi local >/dev/null 2>&1 || true
  rm -rf -- "$test_tmp"
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

docker compose -p "$MNEMONIC_E2E_COMPOSE_PROJECT" -f "$base_compose" -f "$nginx_compose" \
  run --rm --no-deps nginx-stock-policy-check
docker compose -p "$MNEMONIC_E2E_COMPOSE_PROJECT" -f "$base_compose" -f "$nginx_compose" \
  up -d --build --wait

assert_single_content_encoding() {
  local headers=$1
  local expected=$2
  local count
  count=$(awk 'BEGIN { IGNORECASE = 1 } /^Content-Encoding:/ { count += 1 } END { print count + 0 }' "$headers")
  if [[ "$count" != "1" ]] || ! grep -Eiq "^Content-Encoding:[[:space:]]*$expected[[:space:]]*$" "$headers"; then
    echo "Expected exactly one Content-Encoding: $expected response header." >&2
    exit 1
  fi
}

# Prove the test edge actually loaded and enabled google/ngx_brotli. Merely
# advertising `br` to a module-free nginx instance would not exercise the
# route's portable identity marker barrier.
curl --fail --silent --show-error \
  --dump-header "$test_tmp/brotli-control.headers" \
  --output "$test_tmp/brotli-control.body" \
  --header "Accept-Encoding: br" \
  "$MNEMONIC_NGINX_E2E_URL/brotli-control"
assert_single_content_encoding "$test_tmp/brotli-control.headers" br

work_id=11111111-1111-4111-8111-111111111111
route="/api/mnemonic/projects/11111111-1111-4111-8111-111111111111"
route+="/work-items/$work_id/completion-evidence"

curl --fail --silent --show-error --dump-header "$test_tmp/identity.headers" --output "$test_tmp/identity.json" --header "Accept-Encoding: gzip, br" "$MNEMONIC_NGINX_E2E_URL$route"

assert_single_content_encoding "$test_tmp/identity.headers" identity
grep -Eiq '^X-DNS-Prefetch-Control:[[:space:]]*off' "$test_tmp/identity.headers"
grep -Eiq '^Cache-Control:.*no-store.*no-transform' "$test_tmp/identity.headers"
python3 -m json.tool "$test_tmp/identity.json" >/dev/null

# Next owns the final identity marker. An acceptable absent upstream coding is
# normalized to explicit identity before nginx can consider a response filter.
curl --fail --silent --show-error \
  --dump-header "$test_tmp/identity-absent.headers" \
  --output "$test_tmp/identity-absent.json" \
  --header "Accept-Encoding: gzip, br" \
  "$MNEMONIC_NGINX_E2E_URL$route?cursor=identity-absent"
assert_single_content_encoding "$test_tmp/identity-absent.headers" identity
python3 -m json.tool "$test_tmp/identity-absent.json" >/dev/null

limit_status=$(curl --silent --show-error \
  --dump-header "$test_tmp/identity-limit.headers" \
  --output "$test_tmp/identity-limit.json" \
  --write-out '%{http_code}' \
  "$MNEMONIC_NGINX_E2E_URL$route?cursor=identity-limit")
if [[ "$limit_status" != "200" ]]; then
  echo "Exact-limit identity response returned $limit_status instead of 200." >&2
  exit 1
fi
assert_single_content_encoding "$test_tmp/identity-limit.headers" identity
limit_bytes=$(wc -c < "$test_tmp/identity-limit.json")
if [[ "$limit_bytes" != "3145728" ]]; then
  echo "Exact-limit response contained $limit_bytes bytes instead of 3145728." >&2
  exit 1
fi
python3 -m json.tool "$test_tmp/identity-limit.json" >/dev/null

over_status=$(curl --silent --show-error \
  --dump-header "$test_tmp/identity-max-plus-one.headers" \
  --output "$test_tmp/identity-max-plus-one.json" \
  --write-out '%{http_code}' \
  "$MNEMONIC_NGINX_E2E_URL$route?cursor=identity-max-plus-one")
if [[ "$over_status" != "502" ]]; then
  echo "Max-plus-one identity response returned $over_status instead of 502." >&2
  exit 1
fi
assert_single_content_encoding "$test_tmp/identity-max-plus-one.headers" identity

for encoded_case in gzip-success gzip-error; do
  status=$(curl --silent --show-error \
    --dump-header "$test_tmp/$encoded_case.headers" \
    --output "$test_tmp/$encoded_case.json" \
    --write-out '%{http_code}' \
    "$MNEMONIC_NGINX_E2E_URL$route?cursor=$encoded_case")
  if [[ "$status" != "502" ]]; then
    echo "Encoded upstream $encoded_case returned $status instead of 502." >&2
    exit 1
  fi
  if grep -Fq 'phase11-encoded-poison-must-not-be-reflected' "$test_tmp/$encoded_case.json"; then
    echo "Encoded upstream content was reflected by the browser proxy." >&2
    exit 1
  fi
  assert_single_content_encoding "$test_tmp/$encoded_case.headers" identity
done

completion_route="/api/mnemonic/projects/11111111-1111-4111-8111-111111111111"
completion_route+="/work-items/$work_id/complete"
python3 - "$test_tmp/completion-exact.json" "$test_tmp/completion-over.json" <<'PY'
import json
import pathlib
import sys

maximum = 1024 * 1024
payload = {
    "expected_version": 1,
    "checkpoint": {
        "prompt": "Deployed browser ingress boundary.",
        "source_client": "dashboard",
        "source_session_id": "nginx-boundary",
        "source_model": None,
        "source_session_url": None,
        "repository_branch": None,
        "verified_against": None,
        "tags": [],
        "source_metadata": {},
    },
    "client_operation_id": "22222222-2222-4222-8222-222222222222",
}
body = json.dumps(payload, separators=(",", ":")).encode()
if len(body) >= maximum:
    raise SystemExit("Completion boundary fixture unexpectedly exceeded its envelope")
exact = body + b" " * (maximum - len(body))
pathlib.Path(sys.argv[1]).write_bytes(exact)
pathlib.Path(sys.argv[2]).write_bytes(exact + b" ")
PY

post_completion() {
  local input=$1
  local output=$2
  shift 2
  curl --silent --show-error --http1.1 \
    --header "Content-Type: application/json" \
    --header "Origin: $MNEMONIC_NGINX_E2E_URL" \
    --header "Expect:" \
    --data-binary "@$input" \
    --output "$output" \
    --write-out '%{http_code}' \
    "$@" \
    "$MNEMONIC_NGINX_E2E_URL$completion_route"
}

exact_status=$(post_completion \
  "$test_tmp/completion-exact.json" \
  "$test_tmp/completion-exact-response.json")
if [[ "$exact_status" != "200" ]]; then
  echo "Exact 1 MiB completion request returned $exact_status instead of 200." >&2
  exit 1
fi
python3 - "$test_tmp/completion-exact-response.json" <<'PY'
import json
import pathlib
import sys

payload = json.loads(pathlib.Path(sys.argv[1]).read_bytes())
if payload != {"accepted_body_bytes": 1024 * 1024}:
    raise SystemExit(f"Unexpected exact-boundary upstream response: {payload!r}")
PY

over_status=$(post_completion \
  "$test_tmp/completion-over.json" \
  "$test_tmp/completion-over-response.json")
if [[ "$over_status" != "413" ]]; then
  echo "1 MiB plus one completion request returned $over_status instead of 413." >&2
  exit 1
fi
if grep -Fq 'accepted_body_bytes' "$test_tmp/completion-over-response.json"; then
  echo "The oversized completion request reached the controlled upstream." >&2
  exit 1
fi

# A declared length above the limit must fail closed before JSON parsing even
# when the transmitted body itself is exactly the accepted boundary.
misleading_length_status=$(post_completion \
  "$test_tmp/completion-exact.json" \
  "$test_tmp/completion-misleading-length-response.json" \
  --header "Content-Length: 1048577")
if [[ "$misleading_length_status" != "413" ]]; then
  echo "Misleading over-limit Content-Length returned $misleading_length_status instead of 413." >&2
  exit 1
fi
if grep -Fq 'accepted_body_bytes' "$test_tmp/completion-misleading-length-response.json"; then
  echo "The misleading over-limit request reached the controlled upstream." >&2
  exit 1
fi

# Transfer-Encoding removes the client Content-Length. The deployed edge and
# Next stream counter must still accept the inclusive limit and reject byte +1.
chunked_exact_status=$(post_completion \
  "$test_tmp/completion-exact.json" \
  "$test_tmp/completion-chunked-exact-response.json" \
  --header "Transfer-Encoding: chunked" \
  --header "Content-Length:")
if [[ "$chunked_exact_status" != "200" ]]; then
  echo "Chunked exact 1 MiB completion request returned $chunked_exact_status instead of 200." >&2
  exit 1
fi
grep -Fq '"accepted_body_bytes":1048576' "$test_tmp/completion-chunked-exact-response.json"

chunked_over_status=$(post_completion \
  "$test_tmp/completion-over.json" \
  "$test_tmp/completion-chunked-over-response.json" \
  --header "Transfer-Encoding: chunked" \
  --header "Content-Length:")
if [[ "$chunked_over_status" != "413" ]]; then
  echo "Chunked 1 MiB plus one request returned $chunked_over_status instead of 413." >&2
  exit 1
fi
if grep -Fq 'accepted_body_bytes' "$test_tmp/completion-chunked-over-response.json"; then
  echo "The oversized chunked completion request reached the controlled upstream." >&2
  exit 1
fi

echo "PASS: stock nginx accepts the shared policy; enabled Brotli remains outside its identity-coded evidence route; nginx/Next enforce the exact 3 MiB response and inclusive 1 MiB completion ingress boundaries."
