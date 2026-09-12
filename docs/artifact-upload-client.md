# Direct artifact uploads from an agent's filesystem

Use `scripts/upload_artifact.py` to upload or replace a local file without sending
base64 through MCP or the model's context. The standard-library client sends raw
bytes to the existing authenticated REST API and prints a compact JSON receipt.
It runs on Linux/macOS with Python 3.10 or later; repository development uses 3.14.
Installed plugins and portable skill exports include the same standalone helper
at `scripts/upload_artifact.py` inside their bundle.

The operator supplies the reachable API **origin** through `--api-url` or
`MNEMONIC_API_URL`, and provisions `MNEMONIC_API_KEY` in the sending process's
environment. The API origin can differ from the MCP origin. Do not infer it from
the MCP URL, read client credential files, print the key, or pass it on the command
line. The helper ignores proxy environment variables and refuses redirects.

## Prepare and send

Resolve the intended project and work IDs with MCP metadata reads. Then prepare
a new request directory outside Git, using your actual session ID and client:

```sh
python3 scripts/upload_artifact.py prepare \
  --api-url https://mnemonic-api.example.com \
  --project-id PROJECT_UUID \
  --source '/absolute/path/report.pdf' \
  --request-dir '/private/client-state/report-upload' \
  --agent-session-id ACTUAL_SESSION_ID \
  --actor-client ACTUAL_CLIENT \
  --description 'Report supporting this work' \
  --work-item-id WORK_UUID

python3 scripts/upload_artifact.py send \
  --request-dir '/private/client-state/report-upload'
```

The parent of `--request-dir` must exist; the directory itself must be new.
`prepare` makes no network calls. It copies bytes to a mode-0600 file in a
mode-0700 directory and records the API origin, metadata, size, SHA-256 and one
locally generated operation UUID in `request.json`. Supply `--client-operation-id`
only when you already have an unused UUID for this intent. Its compact output
identifies the prepared request; it does **not** mean the file was uploaded.

The filename defaults to the source basename; `--filename` supplies an explicit
safe basename. Repeat `--related-work-item-id` and `--related-artifact-id` for
known relationships. `--sensitive` sets the human-approval classification.
Omitting optional fields keeps them omitted. The API enforces its filename,
project relationship, availability and configured size policies. The helper
supports up to 1 GiB; the API default remains 64 MiB. An authenticated
`GET /api/v1/artifacts/status` or MCP metadata read reports the current limit.

`send` makes one request, using the frozen API origin and an independently
verified copy of the prepared bytes. It never revisits the source file or retries
automatically. The response must match the operation UUID, artifact/project,
revision, filename, size, checksum and supplied metadata before success is printed.
Only IDs, revision, size, checksum and the receipt replay flag enter stdout;
response descriptions, file bytes, base64 and credentials do not.

## Replacement and recovery

Before replacing, read `get_artifact` to obtain the current revision and original
filename. Prepare a new directory with `--artifact-id ARTIFACT_UUID`,
`--expected-revision REVISION`, and `--filename ORIGINAL_FILENAME` in addition to
the source and caller fields above. `send` then uses
`PUT /api/v1/projects/{project_id}/artifacts/{artifact_id}/content`; creation uses
`POST /api/v1/projects/{project_id}/artifacts`.

Replacement permanently removes old bytes. Omitted description, sensitivity and
work links preserve existing values; supplied related IDs add durable links.
Use `--no-sensitive` only for an authorized classification change, never to
bypass approval for sensitive content access.

After a timeout, disconnect, or unverifiable success, retain the entire prepared
directory unchanged and run `send` against it **at most once more**. Never rerun
`prepare`, edit `request.json`, replace its `content`, or substitute a new UUID
for an uncertain intent. If the second outcome is unknown, stop sending and
reconcile using `get_artifact` and `list_artifact_history`; the prepared summary
contains the target artifact ID even if the first response was lost. A receipt
replay is the original historical result: reread metadata before further edits.
A classified storage failure requires operator repair before any retry.
A definitive revision conflict requires a fresh metadata read and a new intent
only if replacement is still authorized. Never put `send` in a retry loop.

Keep the directory private: it contains the original file, even after the source
or server content changes. Remove it only after the outcome is settled and no
exact retry is needed. A failed `prepare` may leave a partial directory but sends
nothing. The helper refuses existing directories and nonregular or symlinked
source files; resolve an intentional symlink to the chosen regular file first.

MCP upload and replacement tools remain available for programmatic clients that
supply base64 outside model context. Agents with local files should use this
helper. If the client environment is missing, request the operator's API origin
and provisioned credential environment instead of reading a large base64 string
into the session.
