# Local artifact upload and replacement

Read `help(topic="authorize_artifact_upload usage")` for the current grant,
metadata, authorization, and retry contract. Use the bundled
[upload helper](${CLAUDE_PLUGIN_ROOT}/scripts/upload_artifact.py) for local files;
it requires Python 3.10+ and streams bytes outside model context.

Resolve the helper from this installed bundle, then prepare a private request:

```sh
python3 /resolved/upload_artifact.py prepare \
  --source '/absolute/source.pdf' --request-dir '/private/new-request' \
  --project-id PROJECT_UUID --agent-session-id ACTUAL_SESSION_ID \
  --actor-client ACTUAL_CLIENT --no-tags
```

Choose tags according to command help; `--no-tags` explicitly declares none.
The parent directory must exist. Pass the prepared `upload_intent` unchanged to
`authorize_artifact_upload`, then save its structured grant directly to a private
file and send:

```sh
python3 /resolved/upload_artifact.py send \
  --request-dir '/private/new-request' --grant-file '/private/grant.json'
```

For replacement, prepare with `--artifact-id`, `--expected-revision`, and
`--filename ORIGINAL_FILENAME` from the current artifact. Keep the prepared
directory unchanged until the outcome is known; follow command help for retries.
Preparation and grant issuance alone do not confirm an upload.

Helpers need no standing API credential with the grant route. Use owner-only
grant files or `--grant-file -` through private stdin. Never embed tokens in shell
source, arguments, URLs, logs, or checkpoints, or inspect credential files.
For downloads, use the [download guide](${CLAUDE_PLUGIN_ROOT}/reference/artifacts.md).
