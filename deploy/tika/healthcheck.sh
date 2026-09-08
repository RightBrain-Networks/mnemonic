#!/bin/bash
# Bash's built-in TCP support avoids installing an additional network client.
set -eu
exec 3<>/dev/tcp/127.0.0.1/9998
printf 'GET /version HTTP/1.0\r\nHost: localhost\r\n\r\n' >&3
IFS= read -r status <&3
case "$status" in
  *' 200 '*) exit 0 ;;
  *) exit 1 ;;
esac
