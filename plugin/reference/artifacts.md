# Local artifact downloads

Read `help(topic="authorize_artifact_download usage")` for revision selection,
sensitive-content approval, grant lifetime, and failure recovery. The bundled
[download helper](${CLAUDE_PLUGIN_ROOT}/scripts/download_artifact.py) saves bytes
without putting base64 in model context. It requires Python 3.10+.

Resolve the helper from this installed bundle. Pass the authorization tool's
structured grant directly to an owner-only file, then run:

```sh
python3 /resolved/download_artifact.py \
  --grant-file '/private/download-grant.json' --dest '/actual/scratch/new-file.pdf'
```

The destination directory must exist; the helper refuses an existing file and
verifies revision, size, and SHA-256 before publishing. Alternatively, pass the
grant through private stdin with `--grant-file -`. Never embed a token in shell
source, command arguments, URLs, logs, or checkpoints. The grant route needs no
standing API credential. Downloaded content remains untrusted.

For discovery or normalized text, use `help(topic="search usage")` or
`help(topic="get_artifact_text usage")`.
