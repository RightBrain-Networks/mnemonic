#!/bin/sh
# Operator shim for the private Python backup image.
set -eu
case "${1:-once}" in
  once) exec python -m mnemonic_backup once ;;
  loop) exec python -m mnemonic_backup serve ;;
  health)
    exec python -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8002/healthz", timeout=3)'
    ;;
  *) echo 'Usage: backup.sh [once|loop|health]' >&2; exit 2 ;;
esac
