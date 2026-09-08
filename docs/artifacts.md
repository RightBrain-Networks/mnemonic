# Project artifact library

Application/API/MCP/dashboard `0.23.0`, plugin `0.19.0`, and migration
`0026_artifact_library` support files outside Git. Each artifact belongs permanently
to one project. Files retain their validated original basename inside
`<artifact root>/<project UUID>/<artifact UUID>/<filename>`. Different artifacts
can have the same filename without colliding.

PostgreSQL stores relative pointers, current metadata, SHA-256, confidently
detected MIME, creator client/session, originating and related work IDs, immutable
revision metadata, an append-only audit log, and durable mutation receipts. It
does not store file bytes. Artifact links are durable and additive; the origin
cannot change, and replacement can add related work. Ordinary work recall embeds
a bounded artifact list; the dashboard work detail links to the filtered library.

## Deployment

Before starting the updated Compose stack, create the private host bind directory:

```sh
sudo install -d -m 0700 -o 10001 -g 10001 ./artifacts
```

Set `MNEMONIC_ARTIFACT_DIR` in `.env` to use another host directory and create it
with the same permissions. Compose explicitly declares a **bind mount**, with
`create_host_path: false`, at `/var/lib/mnemonic/artifacts` inside the API. It creates
no artifact Docker volume. Only the API mounts artifact storage. The application
itself uses `MNEMONIC_ARTIFACT_ROOT` and accepts any suitable filesystem mount
provided by its runtime; it does not test whether Docker used a bind or a volume.

Set `MNEMONIC_ARTIFACT_MAX_BYTES` in `.env` to the streamed upload limit, default
67,108,864 bytes (64 MiB). Positive values from 1 through 1,073,741,824 bytes (1 GiB)
enable the library; **0 disables the whole artifact subsystem**. Compose forwards
the same setting to API and dashboard. Recreate these services after changing it
(`docker compose up -d api web`); this is startup configuration, not a live setting.
MCP discovers the API's authoritative status before each artifact tool operation.

Disabled mode rejects artifact reads, writes, downloads and even content-search
stubs with `artifact_library_disabled` (HTTP 503, `context.max_bytes=0`). It does
not initialize artifact storage, run recovery/cleanup, or include artifacts in
work context. Existing files, metadata, audit history, pending intents and receipts
are retained unchanged and become available when reenabled. The Artifacts menu
remains visible with an explicit disabled message. The Compose bind mount still
requires its host directory to exist even when the application feature is disabled.

The nginx example permits the 1 GiB binary transport ceiling without buffering;
the API enforces the configured limit and disabled mode. MCP base64 transfers
retain an independent 64 MiB decoded-content ceiling for memory safety, even if
the upload setting is larger; use the binary API/dashboard above that ceiling.
Its example ingress permits 90 MiB
for base64/JSON overhead. MCP admits at most two concurrent artifact HTTP
transfers and bounds their total duration; ordinary calls release their ingress
slot after validation. Large transfers have base64 and SDK memory overhead; the
binary REST endpoint is more efficient.

Oversized fresh uploads report HTTP 413 `artifact_too_large` with the actual
configured byte limit in the message and `context.max_bytes`. Lowering a positive
upload limit does not prevent existing downloads or exact replay of completed
receipts with larger original bytes. Transfer-specific ceilings still apply.
Disabling temporarily blocks even receipt replay; preserve uncertain operations'
UUIDs and exact arguments/bytes until reenabling, rather than creating new intents.

Quiesce and upgrade the API, MCP adapter, and dashboard together. The existing
database backup job backs up artifact metadata only. It does not copy file bytes
or preserve old content. A database restore does not restore files. Keep database
and current filesystem state coordinated during operator restore procedures;
missing or mismatched content is refused instead of silently serving another
revision. No older application process should run against this schema.

## Retention and recovery

Replacement requires the revision just read and the unchanged original filename.
It atomically publishes new bytes, increments revision, and retains previous
metadata. The previous file content is not retained. Deletion unlinks current
content while retaining all metadata and audit history. There is no content undo,
revision download, trash, or application-managed file backup. Filesystem snapshots,
storage-device remanence, or bytes already downloaded by clients are outside these
application retention guarantees.

The filesystem and PostgreSQL cannot share a transaction. The API first stages
bounded bytes, commits a durable operation intent, then publishes and finalizes
the revision, audit, and receipt under a lock. Reads and subsequent writes recover
pending operations before accessing that artifact. Library and work-context discovery
omit artifacts with pending intents until recovery finishes; counts and pagination
cover only the remaining records. Recovery commits each artifact independently, so
one failed intent does not block healthy artifacts or other projects. Direct metadata,
history, and content reads fail when their requested artifact cannot recover, rather
than returning stale metadata. Maintenance also recovers pending intents and cleans
abandoned staging files. Cleanup preserves every pending intent's stage and continues
after recovery failures whenever the operation journal can still be read. A successful mutation
has published/unlinked content and a durable receipt. If the process stops between
those steps, recovery completes the same intent; it does not restore old bytes.

Every upload, replacement, and deletion needs a caller-generated operation UUID.
Retain the exact metadata and bytes with it before sending. After an uncertain
response, retry the exact request with the same UUID at most once, then reconcile
through metadata/history reads. Never substitute a new UUID for an uncertain
intent. Replaying a completed receipt returns the original metadata, even after
later changes. Revision conflicts are definitive and require a fresh read before
preparing another intent. Artifact receipts use `artifact_operations`; existing
work receipts remain in `client_operations`.

## REST and MCP

All REST paths below are relative to `/api/v1/projects/{project_id}` and require
the existing Bearer API key. The dashboard uses its same-origin `/api/artifacts/`
proxy, which validates origin and transport headers before forwarding credentials.

The project-independent `GET /api/v1/artifacts/status` is authenticated and
`no-store`, and remains available when disabled. It returns `enabled`, `max_bytes`
and an explicit `message`. All eight MCP artifact tools preflight this status;
disabled attempts produce an explicit tool error without accessing artifact data.
Successful results retain their existing fields and add `artifact_library` with
`enabled`, configured `max_bytes`, `mcp_transfer_max_bytes`,
`effective_upload_max_bytes`, and a human-readable message. Errors also state the
last observed limit when known. A failed status check sends no artifact operation
but does not resolve an earlier unknown write. Complete MCP tool calls exceeding
the content allowance receive a correlated error result with explicit limits,
without dispatching the operation. Safe correlation requires a complete frame
within the 90 MiB wire ceiling, a literal base64 field whose removal leaves at
most 1 MiB of valid JSON, and a valid bounded tool request ID. Hard-cap, incomplete,
malformed, or excessive-metadata frames receive static transport refusals; SDK
clients may expose only connection closure or HTTP 413 for those invalid frames.
Use a metadata-only artifact call to discover configuration before a large transfer.

| REST endpoint | MCP tool | Behavior |
| --- | --- | --- |
| `GET /artifacts` | `list_artifacts` | Search current/historical metadata and audit text; filter by work, sort and page |
| `GET /artifacts/{id}` | `get_artifact` | Read current metadata, including deleted records |
| `GET /artifacts/{id}/history` | `list_artifact_history` | Page/search revision metadata and audit events |
| `POST /artifacts` | `upload_artifact` | Upload raw bytes; return revision 1 |
| `PUT /artifacts/{id}/content` | `replace_artifact` | Atomically replace bytes at an expected revision |
| `GET /artifacts/{id}/content` | `download_artifact` | Download current content; optional `expected_revision` pins the read |
| `DELETE /artifacts/{id}` | `delete_artifact` | Remove bytes and return retained metadata |
| `POST /artifacts/search-content` | `search_artifact_contents` | Explicitly unimplemented; REST returns 501, MCP returns `status: unimplemented` |

List query parameters: `q` (up to 200 characters), `work_item_id`,
`include_deleted`, `sort` (`filename`, `created_at`, `modified_at`, `size_bytes`,
`revision`), `order` (`asc`/`desc`), `limit` (1–100), and `offset` (0–1,000,000).
History accepts `q`, `limit`, and `offset`, returning independent `revisions` and
`audit` pages. Search never parses or indexes file contents.

Binary mutations use `Content-Type: application/octet-stream`,
`X-Client-Operation-ID: <UUID>`, and an ASCII JSON `X-Artifact-Metadata` header
(maximum 16 KiB). Upload/replacement metadata has `filename`, optional
`description`, `agent_session_id`, `actor_client`, originating `work_item_id`,
and up to 50 `related_work_item_ids`. Encode non-ASCII JSON strings using Unicode
escapes. Replacement and deletion also require `X-Artifact-Expected-Revision`.
Deletion's metadata contains actor/session only. Node and Uvicorn startup use
a 64 KiB header envelope, and nginx uses four 32 KiB large-header buffers so
the maximum accepted metadata header fits with its framing and other headers.
Use the same limits for custom process launchers. Metadata stays out of upload
query URLs. MCP requires truthful session/client provenance and uses canonical
`content_base64` for upload/replacement; downloads return base64 and metadata.

## Untrusted content

Storage rejects traversal, separators, hidden/reserved names, control and bidi
characters, unsafe punctuation, and oversized filenames. Safe basenames are
unchanged. UUID directory components are server controlled; descriptor-relative
operations reject symlinks and nonregular or multiply linked files. Directories
are private and files have no execute permission. Streaming enforces actual byte
limits even without a trustworthy Content-Length. MIME metadata uses bounded
content signatures; a detected type does not establish safety.

Downloads use `application/octet-stream`, attachment disposition, `nosniff`,
and no-store headers. The application never executes files, extracts archives,
renders documents inline, scans them with third-party services, or interprets
their embedded instructions. These controls follow the relevant filename,
storage, permission, and size-limit guidance in the
[OWASP file upload guidance](https://cheatsheetseries.owasp.org/cheatsheets/File_Upload_Cheat_Sheet.html),
while retaining arbitrary file types and safe original basenames as required.

PII may occur in bytes and metadata. Filenames, descriptions, checksums and audit
provenance remain searchable after deletion; use descriptions that expose only
what discovery needs. Mnemonic's existing shared-key/private-dashboard trust
boundary still applies: project scoping is not per-user authorization. Limit
host access and dashboard access accordingly. There is no malware-safety claim
or content-at-rest encryption supplied by this feature.

The dashboard's Artifacts item sits below Needs Attention. The directory supports
upload, download, replacement, deletion, sortable columns, paging, dropping files,
and clipboard file paste. Unknown mutation outcomes retain the exact selected
File and intent for deliberate retry. Navigating away warns when that intent
would be lost.

Validated directory layouts: [desktop](images/artifacts-desktop.png) and
[narrow screen](images/artifacts-narrow.png).
Disabled-state layouts: [desktop](images/artifacts-disabled-desktop.png) and
[narrow screen](images/artifacts-disabled-narrow.png).
