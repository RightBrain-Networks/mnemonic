#!/bin/sh
# Explicit operator commands inside the shared worker image.
set -eu
case "${1:-once}" in
  once) exec python -m mnemonic_backup once ;;
  loop) exec uvicorn mnemonic_api.job_worker:create_app --factory --host 0.0.0.0 --port 8002 --no-access-log ;;
  health)
    exec python -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8002/healthz", timeout=3)'
    ;;
  *) echo 'Usage: backup.sh [once|loop|health]' >&2; exit 2 ;;
esac
