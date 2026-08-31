#!/bin/sh
set -eu

# Start Mnemonic with the public HTTPS host/origin allowlists as well as the
# local defaults. Keep this wrapper as the normal way to bring up the stack.
root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec docker compose --project-directory "$root" \
  -f "$root/compose.yaml" \
  -f "$root/compose.tls.yaml" \
  up --build -d --wait "$@"
