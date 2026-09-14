# Operation-scoped artifact upload grants

The local upload helper needs bytes and one authorized upload intent, not the
deployment-wide bearer key. `authorize_artifact_upload` is available over the
already authenticated MCP connection. It returns a five-minute capability and a
client-reachable HTTP MCP endpoint. Issuance performs an ordinary artifact status
read and makes no domain mutation or journal reservation.

## Authority and exact intent

A domain-separated HMAC-SHA-256 under the MCP service's private API key signs the
expiry and canonical JSON of the project, operation UUID, upload/replacement
target, expected revision, complete supplied metadata, size and SHA-256. Omission
in metadata remains omission. The raw API credential never leaves the server.
The opaque token contains only a version, expiry and signature.

The helper sends raw bytes to the MCP endpoint using
`Authorization: MnemonicUpload TOKEN` and an ASCII JSON
`X-Artifact-Upload-Intent` header. This authorization scheme is accepted only for
POST on the MCP endpoint. Ordinary RPCs still require the shared bearer key.
Host and Origin validation precedes either authentication path. No client-selected
API URL, REST method, upstream headers, or filesystem path is accepted.

A valid grant permits one mutation, not one HTTP attempt. The existing durable
`artifact_operations` journal already prevents a second mutation under the same
project/operation UUID and refuses an intent conflict. Repeated exact requests
return its receipt. Expiry stops a new transfer attempt; an authenticated caller
may reissue a grant for the unchanged intent to recover a lost response. Key
rotation invalidates outstanding grants; reauthorization with the current MCP
connection preserves the original operation UUID and receipt recovery.

Grants add no project-level user identity model. Authenticated issuers hold the
existing shared deployment authority. The issued capability narrows what the
receiving helper can do to the exact signed mutation. Sensitive-read approval
tokens and human-approval rules remain independent and unchanged.

## Bytes, resource limits and response handling

The gateway validates the grant before reading a body. It requires an exact
Content-Length and rejects content/transfer encoding and conflicting artifact
control headers. Two admitted transfers each have a 300-second overall deadline
and at most a 1-GiB private temporary file. Admission waits at most 100 ms.
The deployment must have space for up to 2 GiB of temporary upload data under the
MCP process's temporary directory. Files close on success, failure or cancellation.

The complete file is size/checksum verified before the gateway opens the existing
raw-byte REST mutation. It verifies expiry again before forwarding. Uploads stream
from that private spool to the API with fixed length and server-owned headers.
The API retains filename, project/link, revision, availability, size, storage,
receipt and transaction guards. A positive limit reduction must still allow
existing receipt replay; grant issuance does not preempt that receipt lookup.

Local staging storage faults return a controlled 503 with an errno-derived cause,
`attempt_not_committed=true` and `storage_boundary=mcp_upload_staging`. This asserts
only that invocation did not reach the API; earlier same-UUID attempts remain
uncertain. The helper stops for operator repair of the MCP temporary directory.

Upstream redirects are refused and responses are bounded to 64 KiB. Error bodies
are reconstructed from allowlisted status/code pairs and bounded policy/storage
context. Upstream prose, diagnostic fields and error headers never pass through. Success is
validated against the signed intent and existing canonical artifact receipt
contract. The helper independently verifies the receipt before reporting success.
The helper allows a 310-second response wait within a 330-second overall request
deadline, accommodating the gateway’s second transfer. Transport errors after
forwarding retain an unknown outcome; no layer silently
retries. The agent retains one exact uncertain retry across grant refreshes.

## Endpoint discovery and deployment

Default HTTP connections return their observed MCP request URL. The existing
Host allowlist constrains that address. Proxy-supplied forwarding headers do not
establish a trusted scheme, host or prefix: configure `MNEMONIC_MCP_PUBLIC_URL`
for proxies, or for stdio adapters that use the deployment's HTTP MCP service.
The supplied TLS Compose overlay defaults this URL from its configured TLS host
and known `/mcp` route. Refresh the nginx example when upgrading: it allows the
raw 1-GiB transport without buffering, retains header limits and network guards,
and waits 310 seconds for a response. The adapter still limits JSON RPCs to 90 MiB.
The configured value is a complete HTTP(S) MCP URL without userinfo, query or
fragment. Remote deployments use HTTPS and their existing authentication boundary.

The tool catalog grows from 54 to 55. Grant issuance is stateless and does not
increase the 17 receipt-protected MCP mutations or 24 REST receipt kinds.
The raw transfer executes the existing upload/replace artifact operations.
No migration or new database journal is needed.
