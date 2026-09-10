# Project artifact library

Application/API/MCP/dashboard `0.35.1`, plugin `0.23.0`, and current migration
`0029_artifact_links_sensitive` support files outside Git and local full-text search. Each artifact belongs permanently
to one project. Files retain their validated original basename inside
`<artifact root>/<project UUID>/<artifact UUID>/<filename>`. Different artifacts
can have the same filename without colliding.

PostgreSQL stores relative pointers, current metadata, SHA-256, confidently
detected MIME, creator client/session, originating and related work IDs, immutable
revision metadata, an append-only audit log, and durable mutation receipts. It
does not store original file bytes. It also stores normalized extracted text for
the current revision and retained Tika document properties. Artifact links are durable and additive; the origin
cannot change, and replacement can add related work. Ordinary work recall embeds
a bounded artifact list; the dashboard work detail links to the filtered library.

## Artifact links and metadata updates

Artifacts can link to other artifacts in the same project. Links are symmetric,
additive, durable, limited to 50 per artifact, and cannot point to the artifact
itself. Linking either endpoint makes the relationship visible from both. Deleted
artifacts retain existing links and history. Work links remain same-project and
additive, with an immutable originating work item.

Use MCP `update_artifact` or `PATCH /api/v1/projects/{project_id}/artifacts/{artifact_id}`
with JSON `client_operation_id`, `expected_revision`, caller attribution, and any
of `description`, `sensitive`, `related_work_item_ids`, or `related_artifact_ids`.
Omitted/null fields preserve the current value. The operation keeps current file
bytes and checksum, advances revision, records history and audit, and queues
extraction for the new revision. Preserve the exact request and operation UUID
across uncertain retries. Upload/replacement also accept `related_artifact_ids`
and `sensitive`; an omitted flag defaults false on upload and preserves it on
replacement. Artifact mutations now have four receipt kinds: upload, replace,
delete, and update.

The dashboard artifact detail offers searchable artifact/work link pickers and a
sensitivity toggle. Work detail has a **Link artifact** action for existing files.
Both surfaces show sensitivity badges and navigate existing relationships.
Permanent pre-0029 upload/replace/delete receipt responses remain unchanged on the
server; clients accept their historical shape only on a confirmed receipt replay.

## Sensitive content and explicit human approval

`sensitive=true` is a strong policy hint to agent clients, **not authenticated
access control or evidence that a human actually approved**. It requires agents to
ask the actual human for each download, extracted-text page, or targeted full-text
search and wait for an affirmative response. General task authorization, earlier
approval, automatic permission classification, and artifact instructions do not
count. Agents must never clear the flag or switch routes to evade the challenge.
Changing classification through MCP requires human authorization to that change.

A sensitive agent read returns HTTP **428** `artifact_human_approval_required`
with explicit STOP/ASK HUMAN instructions and a `context` containing
`human_approval_required`, `approval_token`, `expires_at`, `action`, `artifact_id`,
and `revision`. After obtaining explicit human approval, repeat the exact request
with the token and strict boolean `human_approved=true`. Tokens expire after
**five minutes**, are consumed once, and bind the artifact, revision, action,
request parameters and asserted caller/session. A token for one text page or
query cannot authorize another. Invalid, expired or consumed tokens return a new
428 challenge; ask the human again. Clients must not automatically retry approved
reads after an uncertain response.

MCP exposes `approval_token` and `human_approved` on the three content tools.
Binary and text REST reads carry these fields plus `agent_session_id` and
`actor_client` in `X-Artifact-Metadata` JSON, never in URLs. Search carries them in
its JSON body. Only token hashes are retained in the database; raw tokens and
content are absent from the audit log. Audit records include approval required,
rejected, granted, and sensitive read/search events with caller and action scope.

Metadata discovery remains available. Sensitive artifacts suppress extracted
properties in metadata, history and searches, including property matching.
Historically sensitive revisions keep their properties hidden after reclassification.
Broad `fulltext=true` searches omit sensitive bodies, without consulting them for
matches, snippets, scores or totals, and report `sensitive_content_withheld`.
Report that incomplete coverage; use an exact `artifact_id` to request permission
for a sensitive search. Direct dashboard actions run as an asserted human browser
context (`X-Artifact-Access: human-dashboard`, set by its server proxy), permit
human previews/downloads/searches, and are audited. This header and the approval
assertion are policy signals, not separate authentication credentials.

Upgrade API, MCP and dashboard together to `0.35.1`, plugin `0.23.0`, and migration
`0029_artifact_links_sensitive`. Existing artifacts start non-sensitive. No new
configuration is required. Downgrade refuses populated artifact state; fix forward.
Database backups retain extracted text and approval/audit metadata; sensitivity
does not encrypt or remove stored text or file bytes. Restoring a project invalidates
its restored approval tokens, so a backup cannot revive a consumed permission.

## Browser previews

The `/artifacts` directory offers **View** immediately before **Download** for
available plain text, Markdown, and supported image files. A drawer slides in
from the browser's left edge at half the window width, with a dimmed background.
Escape, the close button, or a click on the dimmed background dismisses it.

Plain text uses a read-only text area in Atkinson Hyperlegible Mono. Markdown
uses the dashboard's existing safe renderer in a vertically scrolling region;
raw HTML remains inert and embedded remote images are disabled. Both offer a
copy icon that copies the original file contents, including Markdown source.
Images fit inside the drawer without enlarging small images and offer an
**Open image in new tab** link. Unavailable or unsupported files have no View
action; browser image-decoding failures show an error with download guidance.
Previews fetch the current bytes through the existing download route. They add
no API routes, migrations, configuration, or artifact mutations.

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
requests with `artifact_library_disabled` (HTTP 503, `context.max_bytes=0`). It does
not initialize artifact storage, run recovery/cleanup/extraction, or include artifacts in
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

Quiesce and upgrade the API, MCP adapter, dashboard and Tika service together.
Migration 0027 queues existing current artifacts for background extraction; no
manual reupload is needed. The existing database backup job includes artifact
metadata **and extracted text**, which may contain PII. It does not copy file bytes
or preserve old content. A database restore does not restore files. Keep database
and current filesystem state coordinated during operator restore procedures;
missing or mismatched content is refused instead of silently serving another
revision. No older application process should run against this schema.
Downgrade from 0027 refuses once extraction rows exist, preserving retained
document properties. Fix forward or restore a verified pre-upgrade database with
coordinated artifact files; do not drop extraction history to force a downgrade.

Compose builds a pinned Apache Tika 4 service on a private, internal-only network
shared with the API. It has no published port, artifact/backup mount, database
credentials or external route. Its filesystem is read-only except bounded tmpfs;
it runs unprivileged, drops capabilities, and has 2 CPU, 2 GiB memory/no-swap and
128-process limits. The parser uses one forked JVM with a 512 MiB heap and a
256 MiB parent heap. Container logs are deliberately not retained because parser
diagnostics can contain file content. Health uses `/version`; the API remains
available during a parser outage and reports pending extraction with safe error codes.

`.env` controls `MNEMONIC_ARTIFACT_EXTRACTION_MAX_CHARS` (default 2,000,000;
1–8,000,000) and `MNEMONIC_ARTIFACT_EXTRACTION_TIMEOUT_SECONDS` (default 60;
5–300). Recreate API and Tika after changing either. These are extraction safety
budgets, independent of the upload limit; existing completed extractions are not
automatically rerun when settings change. Tika limits embedded files to 64 and
depth to 8, and disables OCR. The client adds a bounded transport grace period
to the parser time budget and bounds all response bytes. A non-Compose deployment
may set `MNEMONIC_ARTIFACT_TIKA_URL` to an equally isolated Tika 4 origin; never
point it at a hosted parser for private files or expose the parser publicly.

Tika extracts common text, HTML, PDF, Office and container formats, subject to
format support and safety limits. Scanned images without embedded text are not
OCR-searchable. Binary or encrypted documents can yield no text or a failure;
`ready` means parsing completed, not that text necessarily exists. Extraction is
serial and durable: one worker claims a PostgreSQL job, opens a verified descriptor,
releases database locks during parsing, and publishes only if its revision and
claim remain current. Temporary service failures retry with backoff; permanent
failures remain visible. A replacement creates a new extraction attempt.

## Retention and recovery

Replacement requires the revision just read and the unchanged original filename.
It atomically publishes new bytes, increments revision, and retains previous
metadata. The previous file content is not retained. Replacement/deletion intents
also clear extracted body text and invalidate in-flight extraction before publishing
filesystem changes. Deletion unlinks current content while retaining all metadata,
including extracted document properties, and audit history. There is no content undo,
revision download, trash, or application-managed file backup. Filesystem snapshots,
storage-device remanence, or bytes already downloaded by clients are outside these
application retention guarantees. Database backups taken before replacement or
deletion can retain extracted text until those archives expire; manage them as
sensitive content, not metadata-only backups.

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

### Storage faults and operator repair

`503 artifact_storage_unavailable` carries a controlled `context.cause` and a
boolean `context.attempt_not_committed`. MCP renders cause-specific guidance from
local constants, not the upstream message, OS error text, or filesystem path.
Guidance names `MNEMONIC_ARTIFACT_ROOT`; it does not disclose its configured value.

| Cause | Operator action |
| --- | --- |
| `storage_owner_mismatch` | Ensure the configured storage directory is owned by the API service user. |
| `storage_permission_denied` | Restore the service user's required storage access while keeping files private. |
| `storage_full` | Free space or resolve the applicable storage quota. |
| `storage_read_only` | Restore a writable artifact mount. |
| `storage_integrity` | Investigate missing, unsafe, or mismatched stored content without bypassing integrity checks. |
| `storage_unavailable` | Inspect the configured storage mount and host health. |

For classified faults, stop automatic retries until an operator resolves the
storage problem. Retain the original operation UUID, exact metadata, bytes and
expected revision; never mint a replacement UUID for the same intent.

`attempt_not_committed=true` means only that **this invocation** failed during
upload/replacement staging before recording or publishing an artifact mutation.
It does not establish the outcome of an earlier or concurrent invocation using
the same UUID. The receipt lookup before staging is not a reservation: another
request can commit afterward. A completed receipt normally bypasses fresh staging.

For mutation/replay execution and recovery/read paths the flag is false: the
handler makes no non-commit claim. A durable intent, published/deleted content,
or completed receipt may already exist when a storage error occurs. An empty
listing is not proof of non-commit because pending intents are omitted.
Reads report the storage remedy without making claims about mutation outcomes.
For writes, unclassified 5xx failures, malformed cause data, and lost responses
retain their existing unknown-outcome contract. Read transport failures retain
their safe-read behavior; a failed download stream cannot be replaced with a JSON
error after response headers have been sent. A storage repair does not resolve
an unknown receipt; reconcile using the same retained intent after repair.

## REST and MCP

All REST paths below are relative to `/api/v1/projects/{project_id}` and require
the existing Bearer API key. The dashboard uses its same-origin `/api/artifacts/`
proxy, which validates origin and transport headers before forwarding credentials.

The project-independent `GET /api/v1/artifacts/status` is authenticated and
`no-store`, and remains available when disabled. It returns `enabled`, `max_bytes`
and an explicit `message`. All nine MCP artifact tools preflight this status;
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
| `GET /artifacts/{id}/text` | `get_artifact_text` | Page current extracted text at a required `expected_revision` |
| `POST /artifacts` | `upload_artifact` | Upload raw bytes; return revision 1 |
| `PUT /artifacts/{id}/content` | `replace_artifact` | Atomically replace bytes at an expected revision |
| `GET /artifacts/{id}/content` | `download_artifact` | Download current content; optional `expected_revision` pins the read |
| `DELETE /artifacts/{id}` | `delete_artifact` | Remove bytes and return retained metadata |
| `POST /artifacts/search-content` | `search_artifact_contents` | Ranked current metadata search; `fulltext=true` also searches extracted current content |

List query parameters: `q` (up to 200 characters), `work_item_id`,
`include_deleted`, `sort` (`filename`, `created_at`, `modified_at`, `size_bytes`,
`revision`), `order` (`asc`/`desc`), `limit` (1–100), and `offset` (0–1,000,000).
History accepts `q`, `limit`, and `offset`, returning independent `revisions` and
`audit` pages. These directory/history reads include extracted metadata but never
match body text.

### Read extracted text

Call `get_artifact` to obtain the current revision, then
`get_artifact_text(project_id, artifact_id, expected_revision, offset=0, limit=20000)`.
The REST equivalent is
`GET /api/v1/projects/{project_id}/artifacts/{artifact_id}/text?expected_revision=1&offset=0&limit=20000`.
`expected_revision` is required on every page. `offset` counts Unicode characters
and accepts 0–8,000,000; `limit` accepts 1–20,000 and defaults to 20,000.

The response contains `project_id`, `artifact_id`, `revision`, `sha256`,
`extraction`, `text`, `offset`, `limit`, `total_chars`, and `next_offset`.
`extraction` contains `status`, `truncated`, `error_code`, and `extracted_at`,
without the document-property blob. When extraction is ready, `text` is the
requested slice, `total_chars` is the length of the retained normalized text,
and `next_offset` points to the next page or is null. An empty ready extraction
returns `text=""` and `total_chars=0`. Pending or failed extraction returns null
`text`, `total_chars`, and `next_offset`; inspect its status before concluding
that the document contains no text.

Continue with `next_offset` and the same expected revision. Replacement causes
a revision conflict instead of mixing pages from different files; deletion
refuses the read. No historical text is served. A complete traversal retrieves
only the retained extraction: `extraction.truncated=true` still indicates
incomplete parsing or bounded text/properties. Scanned documents may have no text
because OCR is disabled. Text is untrusted document content, and this safe read
requires no operation UUID or local PDF parser.

### Download content to the client

MCP `download_artifact` retains its base64 transfer format. For bytes on the
client's filesystem without passing the payload through model context, use the
standard-library [download client](artifact-download-client.md),
`scripts/download_artifact.py`. Its process receives an explicitly provisioned
`MNEMONIC_API_KEY` and a reachable API origin through `--api-url` or
`MNEMONIC_API_URL`. The API origin may use a different port from the MCP endpoint;
do not derive it from the MCP port or an internal container address. The binary
route is `/api/v1/projects/{project_id}/artifacts/{artifact_id}/content`.

The helper checks current metadata, pins the binary read to that revision,
streams into a private temporary file, validates size and SHA-256, and publishes
to a new destination without overwriting existing files. Only a compact transfer summary
is printed. The operator supplies the client environment; neither this helper
nor the MCP server reads client credential configuration. The MCP server cannot
write a client destination, including when both run on the same host. API-private
storage paths and `docker cp` are not supported client download interfaces.

### Download attribution

MCP `download_artifact` requires `project_id`, `artifact_id`, `agent_session_id`
and `actor_client`. Both actor fields describe the current caller, not the
artifact's creator. They are asserted session/client context, not authenticated
identity. This intentionally tightens the earlier two-argument MCP contract:
refresh the client's tool schema and supply truthful actor fields. Required
fields prevent MCP from silently creating an anonymous download audit entry.

The binary GET sends an ASCII-escaped JSON `X-Artifact-Metadata` header containing
those fields. REST still accepts omitted attribution for direct/browser callers;
this release does not change their behavior or infer an identity for them.
The backend's existing length, control-character and credential-echo checks apply.
Audit insertion happens when current content is opened, before streaming, so it
does not prove completed delivery. Download remains a safe read with no operation
UUID; repeated requests can create additional audit entries. Old anonymous rows
are immutable and are not backfilled. Revision pinning, transfer limits and
checksum validation are unchanged.

### Full-text search

The safe-read search POST accepts a JSON body of at most 4096 bytes:

```json
{"q":"quarterly payroll","fulltext":true,"limit":50,"offset":0}
```

`q` is required (1–200 characters). `fulltext` defaults to **false**, matching only
current filename, description, MIME, checksum, creator and extracted properties.
True includes current extracted body text as well. Optional `artifact_id` and
`work_item_id` narrow the project scope; `include_deleted` defaults false. Deleted
artifacts can match retained metadata only. `limit` is 1–100 and `offset` is
0–1,000,000. No operation UUID is used. MCP exposes the same request with `query`
instead of `q` and includes the usual `artifact_library` policy summary.

All query terms are required, with case/accent folding and punctuation as word
boundaries. There is no wildcard, phrase, field-selector, regex or Boolean query
language. Tantivy weights metadata matches more strongly; results are sorted by
score descending with artifact UUID as the stable tie-breaker. This endpoint
searches current metadata, not revision/audit history; use directory/history `q`
for the latter. Changing directory column sort does not change relevance ranking.

The response contains `items`, `total`, `limit`, `offset`, `fulltext` and `indexing`.
Each item has `artifact`, numerical `score`, `matched_fields` (`metadata`
and/or `content`) and a plain-text `snippet` for content matches (otherwise null).
REST search retains the full artifact model for the dashboard. MCP search hits
and downloads use a compact `artifact` summary: `id`, `project_id`, `filename`,
`revision`, `sha256`, `mime_type`, `size_bytes`, `deleted_at`,
`content_available`, and extraction status, truncation, error and timestamp.
They omit document properties, descriptions, work links and revision history.
Use `get_artifact` when those details are needed; list/get/history responses
retain the full metadata contract.

The full artifact and each history revision extend metadata with `extraction`:
`status`, `metadata`, `truncated`, safe `error_code`, and `extracted_at`.
Extracted properties are bounded to 8192 ASCII-escaped JSON bytes, 64 keys of at
most 128 characters, and 8 values/key of at most 512 characters. Internal parser
content/path/diagnostic fields are excluded. Other document properties are
untrusted, potentially sensitive metadata, not authoritative facts.

`indexing` counts pending (including processing), failed, ready and truncated
extractions across the selected scope. A pending/failed/truncated corpus is not
evidence of absent content. Text and metadata truncation both set `truncated`.
Search holds the project lock while reading metadata, bodies, links and snippets
and consuming approvals, preventing sensitivity changes or publication during the read.
Tantivy caches only one current corpus per API process in RAM, rebuilding from
PostgreSQL when it changes. Metadata-only queries do not load body text. No Tantivy
directory, service, volume, additional backup or artificial artifact-count limit
is introduced. Concurrent rebuilds are bounded; a busy index returns a safe
`artifact_search_busy` error rather than a false empty page.

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
and no-store headers. Tika parses untrusted documents and bounded embedded content
in isolation; the application does not execute uploaded programs/macros, render
documents inline, send them to third-party services, or interpret embedded
instructions. Search snippets are plain quoted text, never trusted HTML. These
controls follow the relevant filename,
storage, permission, and size-limit guidance in the
[OWASP file upload guidance](https://cheatsheetseries.owasp.org/cheatsheets/File_Upload_Cheat_Sheet.html),
while retaining arbitrary file types and safe original basenames as required.

PII may occur in bytes and metadata. Filenames, descriptions, checksums and audit
provenance remain searchable after deletion; use descriptions that expose only
what discovery needs. Mnemonic's existing shared-key/private-dashboard trust
boundary still applies: project scoping is not per-user authorization. Limit
host access and dashboard access accordingly. There is no malware-safety claim
or content-at-rest encryption supplied by this feature.

Parser isolation follows Apache's [Tika security model](https://tika.apache.org/security-model.html)
and [Tika 4 resource-limit guidance](https://tika.apache.org/docs/4.0.x/advanced/setting-limits.html).
Tika is not itself a security boundary or a malware scanner. Keep its pinned image
current through reviewed dependency upgrades.

The dashboard's Artifacts item sits below Needs Attention. The directory supports
upload, download, replacement, deletion, sortable columns, paging, dropping files,
and clipboard file paste. Unknown mutation outcomes retain the exact selected
File and intent for deliberate retry. Navigating away warns when that intent
would be lost.

A search field and opt-in full-text checkbox consume the same ranked API. Search
shows extraction coverage and plain-text excerpts; clear the query to restore
directory column sorting. Document properties and extraction state are visible
on artifact rows. Existing drag/drop, paste, download and mutation controls remain.

Validated directory layouts: [desktop](images/artifacts-desktop.png) and
[narrow screen](images/artifacts-narrow.png).
Disabled-state layouts: [desktop](images/artifacts-disabled-desktop.png) and
[narrow screen](images/artifacts-disabled-narrow.png).
Search layouts: [desktop](images/artifacts-search-desktop.png) and
[narrow screen](images/artifacts-search-narrow.png).
