#!/bin/sh
set -eu
umask 077

interval=${MNEMONIC_BACKUP_INTERVAL_SECONDS:-86400}
case "$interval" in
  ''|*[!0-9]*) echo 'Backup interval must be an integer number of seconds.' >&2; exit 2 ;;
esac
if [ "$interval" -lt 60 ]; then
  echo 'Backup interval must be at least 60 seconds.' >&2
  exit 2
fi

backup_once() {
  stamp=$(date -u +%Y%m%dT%H%M%SZ)
  partial=$(mktemp "/backups/.mnemonic-${stamp}.partial.XXXXXX")
  suffix=${partial##*.}
  target="/backups/mnemonic-${stamp}-${suffix}.dump"
  trap 'rm -f "$partial"' EXIT HUP INT TERM
  pg_dump --format=custom --no-owner --no-acl --file="$partial"
  pg_restore --list "$partial" >/dev/null
  # Keep previous dumps even if a clock/PID repeats across container restarts.
  mv -n "$partial" "$target"
  if [ -f "$partial" ]; then
    echo 'Archive name already exists; the previous backup was left intact.' >&2
    exit 1
  fi
  date -u +%s > /backups/.last-success
  trap - EXIT HUP INT TERM
  echo "Saved and checked $(basename "$target")"
}

case "${1:-loop}" in
  health)
    [ -f /backups/.last-success ] || exit 1
    last=$(cat /backups/.last-success)
    case "$last" in ''|*[!0-9]*) exit 1 ;; esac
    now=$(date -u +%s)
    [ "$((now - last))" -le "$((interval + 300))" ]
    ;;
  once)
    backup_once
    ;;
  loop)
    while :; do
      backup_once
      sleep "$interval" &
      sleeper=$!
      trap 'kill "$sleeper" 2>/dev/null || true; exit 0' TERM INT
      wait "$sleeper"
      trap - TERM INT
    done
    ;;
  *) echo 'Usage: backup.sh [once|loop|health]' >&2; exit 2 ;;
esac
