# Download artifacts into a client workspace

`scripts/download_artifact.py` streams authenticated artifact bytes directly into
a new local file. It runs on Linux or macOS using Python 3.14's standard
library; no virtual environment, PDF package, base64 response, or model-generated
file payload is needed.

An operator must provision `MNEMONIC_API_KEY` in the process environment of the
client that will run the script. Use the existing deployment credential through
the operator's normal secret provisioning. The script never reads client
configuration files, accepts a key in a command argument, or prints the key.
Provisioning must happen before the agent session needs the download; the script
cannot acquire a credential from an MCP connection on its own.

Also provide `MNEMONIC_API_URL`, or pass `--api-url`, with the REST API origin
reachable from that client. For a default Compose deployment this is
`http://127.0.0.1:8000` on the deployment host; use the configured
`MNEMONIC_API_PORT` when overridden (for example, `4771`).
The MCP origin and dashboard origin are different services. An internal address
such as `http://api:8000` is only usable inside the corresponding container
network. Use an HTTPS API origin for remote access. The URL must contain only
the scheme, hostname and optional port; the script supplies `/api/v1/...`.

Run from the repository root, with the actual project, artifact and current
session identifiers supplied by the caller:

```sh
python3.14 scripts/download_artifact.py \
  --project-id "$PROJECT_ID" \
  --artifact-id "$ARTIFACT_ID" \
  --dest ./statement.pdf \
  --agent-session-id "$AGENT_SESSION_ID" \
  --actor-client claude-code
```

Use the caller's real client name and session ID, not the uploader's provenance.
Add `--expected-revision 3` when the download must match a revision already read.
Otherwise the script fetches current metadata and pins the following binary GET
to that revision. The binary request carries the caller's provenance in an
ASCII-escaped JSON `X-Artifact-Metadata` header. The API retains its ordinary
download audit record. Repeating a failed download may add another download
audit entry; it does not replace the artifact or require an operation UUID.

Success prints one small JSON object with `path`, `revision`, `sha256` and
`size_bytes`. The file is published only after the response revision, ETag,
length and SHA256 match the metadata. Temporary files have owner-only read/write
permissions and are removed on handled failures. Publication is atomic and
refuses an existing destination, including one created during the transfer.
Choose a new destination for another attempt after a successful download.

The client refuses HTTP redirects and ignores proxy environment variables. Each
request has a 120-second wall-clock deadline, including connection setup,
response headers and streamed reads, plus a 30-second socket timeout. POSIX
interval timers interrupt the request directly; no background download survives
a reported timeout. The client requires main-thread execution and refuses an
inherited interval timer. Its timer is cancelled after response verification and
before publishing the destination. Metadata is limited to 64 KiB, and bytes to
the API's 1 GiB maximum. A disabled library, missing content, revision race, invalid
response or failed verification leaves the destination unpublished. Errors
report a safe diagnostic without response bodies or artifact content. The script
does not parse or execute downloaded files; their bytes remain untrusted input.
