# Local artifact downloads through MCP

Use `authorize_artifact_download` and `scripts/download_artifact.py --grant-file`
from MCP-only sessions. The helper needs no standing API key or API origin.
The same helper ships in the plugin and portable skills; it uses only the Python
standard library and runs on Linux/macOS with Python 3.10+. Repository development
uses Python 3.14.

Read `get_artifact` for the intended current revision, then authorize through MCP
with `project_id`, `artifact_id`, `expected_revision`, and your actual
`agent_session_id` / `actor_client`. Save the exact structured result directly as
an owner-only JSON file, or pass it through private stdin using `--grant-file -`.
Do not interpolate grant tokens into shell source, command arguments, URLs, logs,
or checkpoints. The MCP tool result necessarily contains the short-lived token.

```sh
python3 scripts/download_artifact.py \
  --grant-file '/private/download-grant.json' \
  --dest '/actual/scratchpad/new-file.pdf'
```

Resolve the helper from the installed skill's resource link, not a guessed plugin
cache version. The destination directory must exist. The helper refuses symlinks
and existing destination files, ignores proxy environment variables, refuses
redirects, checks revision/size/SHA-256, and atomically publishes a mode-0600 file.
Only its compact path/revision/size/checksum summary enters model context.

The grant is valid for five minutes and one download of one project/artifact
revision. PostgreSQL stores only its token hash and durably consumes it when the
API opens the pinned file, so MCP replicas and restarts cannot replay it. A changed
revision, deletion, expiry, or previous consumption rejects redemption. Consumption
and download auditing do not prove completed network delivery. Project backups
exclude grants, and restoring a project revokes its existing grants. On an uncertain
transfer, inspect the destination, then obtain a new grant if needed.

Sensitive grants preserve the existing HTTP 428 approval challenge. Stop and ask
the actual human for this exact download, then repeat unchanged authorization with
`approval_token` and `human_approved=true`. Approval is consumed at issuance;
redemption uses that approved, one-use capability. Each new sensitive grant needs
a new explicit human approval. Do not clear sensitivity or switch routes to bypass it.

The helper redeems via `GET /mcp` with `Authorization: MnemonicDownload TOKEN` and
a bounded `X-Artifact-Download-Intent` header. Host/origin guards still apply. The
MCP gateway authenticates upstream with its own configured API credential, spools
and verifies at most 1 GiB, and forwards only validated raw bytes. It accepts no
arbitrary upstream URL or MCP operation. Grant issuance uses authenticated
`POST /api/v1/projects/{project_id}/artifacts/{artifact_id}/download-grants`;
this safe read has no artifact operation receipt. Redeeming uses the existing
binary content route with `X-Artifact-Download-Grant`. Deploy API and MCP together
after migration `0047_artifact_transfer`.

For stdio or a reverse proxy, configure the existing `MNEMONIC_MCP_PUBLIC_URL` to
the reachable HTTP MCP endpoint; forwarded headers are not trusted as destinations.

The optional direct API mode remains available. It requires **both**
`MNEMONIC_API_URL` (reachable API origin) and `MNEMONIC_API_KEY` in the helper's
environment, plus project/artifact IDs and truthful session/client arguments.
The helper reports both missing settings together. Ask the operator to provision
them when required; never read client credential files or guess ports. The direct
mode's sensitive approval policy is unchanged. See the installed
[transfer procedure](../plugin/reference/artifact-transfers.md).
