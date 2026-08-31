#!/bin/sh
set -eu

if [ "${MNEMONIC_CONFIRM_RESTORE:-}" != 'replace-mnemonic-data' ]; then
  echo 'Restore replaces the database contents. Stop API, MCP, web, and backup first.' >&2
  echo 'Set MNEMONIC_CONFIRM_RESTORE=replace-mnemonic-data to authorize this operation.' >&2
  exit 2
fi

name=${MNEMONIC_RESTORE_FILE:-}
case "$name" in
  ''|*[!a-zA-Z0-9._-]*|.*) echo 'Supply a dump filename from /backups, without directories.' >&2; exit 2 ;;
  *.dump) ;;
  *) echo 'The backup filename must end with .dump.' >&2; exit 2 ;;
esac
file="/backups/$name"
[ -f "$file" ] || { echo 'Backup file not found.' >&2; exit 2; }
pg_restore --list "$file" >/dev/null
# A single transaction makes an unsuccessful restore roll back rather than
# leaving a half-restored store. --clean affects only objects in this backup.
pg_restore --dbname="$PGDATABASE" --clean --if-exists --no-owner --no-acl \
  --single-transaction --exit-on-error "$file"
echo 'Database restored. Start the application to apply any newer migrations.'
