#!/bin/sh
set -eu
umask 077

# Only bounded decimal settings enter the parser configuration. No filename,
# uploaded content, arbitrary JSON or client-supplied parser option is accepted.
extract_chars=${MNEMONIC_ARTIFACT_EXTRACTION_MAX_CHARS:-2000000}
extract_seconds=${MNEMONIC_ARTIFACT_EXTRACTION_TIMEOUT_SECONDS:-60}
case "$extract_chars:$extract_seconds" in
  *[!0-9:]*|:*|*:) echo 'Invalid artifact extraction settings' >&2; exit 1 ;;
esac
if [ "${#extract_chars}" -gt 7 ] || [ "${#extract_seconds}" -gt 3 ] ||
   [ "$extract_chars" -lt 1 ] || [ "$extract_chars" -gt 8000000 ] ||
   [ "$extract_seconds" -lt 5 ] || [ "$extract_seconds" -gt 300 ]; then
  echo 'Artifact extraction settings outside safe bounds' >&2
  exit 1
fi
extract_millis=$((extract_seconds * 1000))
sed -e "s/__MAX_CHARS__/$extract_chars/g" -e "s/__TIMEOUT_MILLIS__/$extract_millis/g" \
  /opt/mnemonic-tika/tika-config.json > /tmp/mnemonic-tika-config.json
exec java -Xms32m -Xmx256m -XX:ActiveProcessorCount=2 -Djava.awt.headless=true \
  -Djava.io.tmpdir=/tmp -Duser.home=/tmp \
  -cp '/opt/tika-server/*:/opt/tika-server/lib/*' \
  org.apache.tika.server.core.TikaServerCli -h 0.0.0.0 \
  -c /tmp/mnemonic-tika-config.json
