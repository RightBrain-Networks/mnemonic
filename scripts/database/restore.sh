#!/bin/sh
# A project UUID must be confirmed explicitly; legacy SQL dumps are unsupported.
set -eu
exec python -m mnemonic_backup restore "$@"
