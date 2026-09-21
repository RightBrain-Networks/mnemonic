# Local artifact uploads through the connected MCP service

An authenticated Mnemonic MCP connection can authorize a local upload or
replacement without giving the helper a standing API key. The standard-library
helper streams raw file bytes to that same HTTP MCP endpoint. Only the prepared
intent, a short-lived grant, and a compact verified receipt enter agent context.
Never print or read base64 to populate an MCP upload call, and never read client
credential files.

The helper ships at `scripts/upload_artifact.py` in the repository, installed
plugin, and portable skill exports. It runs on Linux/macOS with Python 3.10 or
later; repository development uses Python 3.14.

## Prepare, authorize, and send

Resolve the intended project and known work/artifact relationships through MCP
metadata reads. Prepare a new private request directory outside Git, using the
actual caller identity:

```sh
python3 scripts/upload_artifact.py prepare \
  --project-id PROJECT_UUID \
  --source '/absolute/path/report.pdf' \
  --request-dir '/private/client-state/report-upload' \
  --agent-session-id ACTUAL_SESSION_ID \
  --actor-client ACTUAL_CLIENT \
  --description 'Report supporting this work' \
  --work-item-id WORK_UUID
```

No API origin or key is required. The parent directory must already exist.
Preparation copies the bytes to a mode-0600 file in a mode-0700 directory,
records their size/checksum, metadata and one operation UUID, and makes no network
request. Its output includes `upload_intent`, identical to `request.json.upload_intent`; this does **not** mean the upload
has completed.

Call `authorize_artifact_upload(intent=PREPARED_UPLOAD_INTENT)` through the
connected MCP tool, copying that entire object unchanged. Save the exact
structured grant result as an owner-only JSON file (mode 0600), separate from the
frozen request directory. Do not put its token in a command argument, URL,
checkpoint, or log. `--grant-file -` also accepts bounded JSON on private stdin;
use the process-input facility or tool-result bridge, never a token-bearing shell
heredoc or `echo` command. Then:

```sh
python3 scripts/upload_artifact.py send \
  --request-dir '/private/client-state/report-upload' \
  --grant-file '/private/client-state/report-upload-grant.json'
```

The helper verifies that the grant matches the frozen intent, streams the
prepared bytes to its exact `upload_url`, and verifies the returned operation
UUID, artifact/project, revision, filename, size, checksum and requested metadata.
It ignores proxy environment variables and refuses redirects. Only compact
receipt IDs, revision, size, checksum and replay status are printed.

The grant lasts five minutes and authorizes **one immutable mutation**, including
its exact receipt replay. It cannot read files, change another project, authorize
different bytes or metadata, or call another MCP/REST operation. A grant is
neither a standing API credential nor proof that the server will accept a fresh
upload under its current domain/storage policies. See the
[grant protocol and security boundary](artifact-upload-grants.md).

## Replacement and recovery

Before replacement, read `get_artifact` for the current revision and original
filename. Prepare a new request with `--artifact-id ARTIFACT_UUID`,
`--expected-revision REVISION`, and `--filename ORIGINAL_FILENAME`. Authorize and
send its exact intent as above. Replacement permanently removes the old bytes;
omitted description, sensitivity and links retain their existing values.
Use `--no-sensitive` only for a human-authorized classification change.

After a timeout, disconnect, or unverifiable success, retain the entire prepared
directory unchanged. Make at most one exact retry of `send`. If the grant has
expired, obtain another grant for the **same upload_intent** and save it in a new
private grant file. Reauthorization does not reset the uncertain-send retry
budget. Never regenerate the operation UUID, edit the manifest, or replace the
prepared bytes to get past an uncertain outcome.

If that retry is also uncertain, stop sending and reconcile through
`get_artifact` and `list_artifact_history`. The prepared `expected_artifact` summary gives the
deterministic target artifact ID even when the first response was lost. Receipt
replay returns the original historical result; read current metadata before a
later edit. A classified storage fault requires operator repair before retry.
A definitive revision conflict requires a fresh metadata read and a new intent
only if replacement is still authorized.

Keep the snapshot private until the outcome is settled and no exact retry is
needed, then remove it and the grant files. Failed preparation can leave a partial
directory but sends nothing. The helper refuses existing request directories and
nonregular or symlinked sources. Resolve an intentional symlink to the selected
regular file first. The helper/gateway support up to 1 GiB; the API's configured
new-upload maximum defaults to 64 MiB. Lowering a positive maximum does not prevent
an otherwise valid existing receipt replay.

## Deployment and existing direct API clients

Ordinary HTTP MCP connections discover their upload endpoint from the
authenticated request itself. For a reverse proxy, configure
`MNEMONIC_MCP_PUBLIC_URL` to the complete client-reachable MCP endpoint, including
its HTTPS scheme and path prefix, alongside the existing Host/Origin allowlists.
The supplied `compose.tls.yaml` sets this from `MNEMONIC_TLS_HOST` and its
known `/mcp` route; refresh the supplied nginx configuration on upgrade to allow
the raw 1-GiB transport. Custom proxies need an explicit endpoint and equivalent
body/header/timeout limits. The service does not trust forwarded headers to
construct a file destination.
A stdio adapter uses that same setting to identify the deployment's running HTTP
MCP service, which must share its API key. This is deployment configuration, not
per-agent credential provisioning. Refresh the MCP tool catalog and installed
skills when upgrading API/MCP/dashboard to 0.53.0 and plugin to 0.31.0.

Existing explicitly provisioned direct API workflows remain supported, including
already prepared requests with uncertain outcomes. Supply `--api-url` or
`MNEMONIC_API_URL` during preparation and `MNEMONIC_API_KEY` to `send` without
`--grant-file`. The operator supplies these values; never infer the API origin
from MCP, read credential files, print the key, or pass it on the command line.
The MCP service's internal `http://api:8000` address is normally usable only inside
its container network. Local agent uploads should use grants.

Programmatic clients may still use MCP `upload_artifact` and
`replace_artifact` with base64 assembled entirely outside model context.
