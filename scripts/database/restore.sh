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

# This application owns the entire public schema and does not install optional
# PostgreSQL extensions. Refuse an unexpected layout instead of deleting data
# outside that boundary or silently producing a partial, hybrid restore.
unexpected_schemas=$(psql --no-psqlrc --tuples-only --no-align --set=ON_ERROR_STOP=1 \
  --command="SELECT nspname FROM pg_namespace
             WHERE nspname <> 'public'
               AND nspname <> 'information_schema'
               AND nspname NOT LIKE 'pg_%'
             ORDER BY nspname")
[ -z "$unexpected_schemas" ] || {
  echo 'Restore refused: the target contains non-public user schemas.' >&2
  echo "$unexpected_schemas" >&2
  exit 2
}
unexpected_extensions=$(psql --no-psqlrc --tuples-only --no-align --set=ON_ERROR_STOP=1 \
  --command="SELECT extname FROM pg_extension WHERE extname <> 'plpgsql' ORDER BY extname")
[ -z "$unexpected_extensions" ] || {
  echo 'Restore refused: the target contains application extensions.' >&2
  echo "$unexpected_extensions" >&2
  exit 2
}

# `pg_restore --clean` only drops objects named by the archive. That leaves
# newer tables (including private receipt rows) behind when restoring an older
# archive. Build the complete SQL first, then replace the application schema
# and restore it in one transaction so either the old database remains intact
# or the archive becomes the complete public schema.
restore_sql=$(mktemp)
trap 'rm -f "$restore_sql"' EXIT HUP INT TERM
{
  echo 'DROP SCHEMA IF EXISTS public CASCADE;'
  echo 'CREATE SCHEMA public AUTHORIZATION CURRENT_USER;'
  echo 'GRANT USAGE ON SCHEMA public TO PUBLIC;'
} > "$restore_sql"
# Keep archive ACLs: Phase 11 revokes PUBLIC function execution and its exact
# owner-only privilege state is part of the audited catalog contract.
pg_restore --no-owner --exit-on-error --file=- "$file" >> "$restore_sql"
psql --no-psqlrc --single-transaction --set=ON_ERROR_STOP=1 --file="$restore_sql"
rm -f "$restore_sql"
trap - EXIT HUP INT TERM
echo 'Database restored. Start the application to apply any newer migrations.'
