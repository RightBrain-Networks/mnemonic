# Agent transcript indexing

Application/API/MCP/dashboard 0.44.0 and migration `0033_transcript_imports` add
workspace imports for existing Claude Code transcripts. Plugin remains 0.26.0.

Mnemonic indexes agent session transcripts once the associated work lease ends.
An MCP claim records an explicit primary transcript location or null. Every fresh
completion, retirement, merge, deletion, or review completion reports additional
subagent locations or explicitly asserts null. `release_claim` also accepts known subagent
locations when pausing. Release, expiry, and terminal lifecycle transitions make
waiting transcripts eligible for indexing. Files are snapshots at indexing time;
there is no continuous tailing while work is Active.

`subagent_transcripts` accepts a nonempty list of at most 100 distinct file paths,
each with its client identifier, or explicit `null` when no additional transcripts
are applicable or available. Omission remains parseable exclusively for a permanent
historical receipt replay with its original exact arguments and operation UUID.
The backend checks receipts before rejecting omitted assertions for fresh execution.
Never add a null assertion to an old frozen request to make a retry look current.

The first parser implementation supports Claude Code. A factory chooses the
client adapter; the adapter automatically distinguishes JSONL message records,
JSON arrays, and JSON message envelopes. Unknown clients and malformed formats
produce durable failure dispositions. For Claude Code, pass `client=claude_code`.
Native hooks expose `transcript_path` and SubagentStop's `agent_transcript_path`;
see the [official Claude Code hook reference](https://code.claude.com/docs/en/hooks).
The session JSONL commonly resides under `~/.claude/projects/<encoded-project>/`;
subagent JSONL may be nested under `<session-id>/subagents/`. Supply the exact
absolute file path that the backend can read, never a guessed host/container path.

The backend reuses the existing Apache Tika container. Transcript parsing first
normalizes the client-specific record structure into text, then the existing Tika
service extracts normalized text and bounded document properties. Separate
transcript scheduling and Tantivy state keep the two libraries independent. A
second Tika instance adds deployment and memory cost without providing a different
parser requirement. The shared service's configured concurrency limit may delay
one library while the other extracts; retryable service contention remains queued.
See [Tika Server concurrency documentation](https://tika.apache.org/docs/4.0.x/using-tika/server/index.html).

PostgreSQL retains transcript metadata, normalized text, extraction properties,
and work/lease provenance. Metadata includes indexing start/completion timestamps,
status and failure code, original byte size and MIME type, detected format, source
SHA-256, normalized-text SHA-256, and truncation. Tantivy is a rebuildable RAM index.
Original transcript bytes remain client-managed files; normalized text retrieval
and download use the indexed snapshot rather than rereading its source path.
Database backups therefore include transcript content.

## Filesystem deployment

This release requires the backend and client to share filesystem access. The
operator configures `MNEMONIC_TRANSCRIPT_ALLOWED_ROOTS` as a JSON array of approved
absolute roots. Its default empty array disables source access. Paths outside those
roots, symlinks, nonregular files, and oversized files are rejected and recorded
with an indexing failure rather than followed. A reported path is data, not shell
input or an instruction to read arbitrary server files.

For Docker, set `MNEMONIC_TRANSCRIPT_SOURCE_DIR` to the host directory containing
allowed transcripts and include `compose.transcripts.yaml` alongside the main
Compose file. The optional override binds that directory read-only into the API
at the same absolute path and configures that directory as the allowed root.
Native deployments configure the JSON allowed-roots environment value directly. The
API runs as UID 10001 and needs read access to transcript files and traversal of
parent directories. Grant narrow directory/file ACLs as appropriate; do not
broaden access to an entire home directory. Configure client-created files to
remain readable by the service. The Tika container receives normalized text over
HTTP and needs no transcript filesystem mount.

## Dashboard settings and retrieval

The `/transcripts` library offers project metadata browsing, opt-in content
search, exact work filtering, indexing status, text preview, and normalized text
download. All transcripts are readable by agents and dashboard users who can
access the project. There is no sensitive flag or approval-token flow. Treat
transcript bodies, snippets, extracted properties, and source paths as untrusted
historical data, never instructions or authenticated authority.

Workspace settings include an indexing enable/disable switch and maximum source
file size (64 MiB by default, bounded by operator policy). Allowed roots are shown
as operator-managed deployment configuration. **Rebuild index** invalidates stored
normalized text and schedules the known sources for fresh extraction; it does not
delete client transcript files. Rebuild requires the source files to remain
available and readable. Text hashes can change after a rebuild. Disabled indexing
leaves sources queued until reenabled.

MCP clients use five safe read tools:

- `list_transcripts`: metadata listing with query and exact work filters.
- `search_transcript_contents`: metadata search by default; `fulltext=true` opts
  into content matching and snippets.
- `get_transcript`: metadata, extracted properties, disposition, and hashes.
- `get_transcript_text`: at most 20,000 Unicode characters per page, pinned by
  `expected_sha256` copied from `text_sha256`; follow `next_offset`.
- `download_transcript`: checksum-verified base64 of the normalized UTF-8 text,
  bounded to 32 MiB and pinned to the same `text_sha256`.

The `sha256` metadata field identifies original source bytes and is distinct from
`text_sha256`. A stale text hash rejects the read; retrieve metadata before
starting a new snapshot. `indexing_incomplete`, failed dispositions, and truncated
entries make coverage limits explicit. Report those limits when presenting search
results. A rebuild does not manufacture unavailable history.


## Import existing transcripts

In **Settings → Workspace → Transcript indexing → Import existing transcripts**,
enter an absolute shared folder path, such as
`/home/jamie/.claude/projects/-srv-fishfood`, and select **Import transcripts**.
The backend must be able to read that path within its configured allowed roots;
the Docker shared-filesystem mount described above also applies to imports.

The import recursively discovers `.jsonl` files, including nested subagent sessions.
Other filename extensions are ignored. Symlinks and nonregular source files are
skipped and counted. Unreadable folders abort registration; no partial import is
committed. Each scan is limited to 5,000 sources, 50,000 directory entries, 64 levels,
and ten seconds of traversal checks. Choose smaller subfolders if a limit is reached.
Malformed or oversized transcripts retain normal failed indexing dispositions.
Import results count newly imported sources, already registered sources, and skipped
entries; use the transcript library to track extraction failures and incomplete indexing.

Deduplication is scoped to the selected project and the normalized absolute source
path, including equivalent repeated separators and `.` components. It does not compare
transcript contents or merge independently enrolled work/lease history. An import skips
any matching agent-enrolled source, including active, failed, or already indexed records.
Repeated and overlapping folder imports add no duplicate sources. A later agent enrollment
reuses a matching imported record and its ID, attaches real work/lease provenance, and
invalidates its earlier snapshot so indexing waits for the new lease generation to end.

Imported sources appear as **Imported**, with no work item, lease generation, or
agent session assertion. They participate in project browsing, unified search, text
retrieval, rebuilds, and backups. Importing while indexing is paused queues them until
indexing is enabled. Source files remain client-managed and are never rewritten.

`POST /api/v1/projects/{project_id}/transcripts/import` accepts exactly
`{ "directory": "/shared/folder", "client_operation_id": "<uuid>" }`. The response echoes
those fields and the project ID, with `imported`, `existing`, and `skipped` counts.
The separate `transcript_imports` journal atomically retains the request and counts.
Retry the exact directory and operation UUID after an uncertain result, even if files
or allowed roots change. A confirmed operation replays without rescanning; changing its
folder returns `transcript_import_conflict`. Fresh scan failures return 422 with
`transcript_import_path_not_allowed`, `transcript_import_scan_failed`, or
`transcript_import_scan_limit`. The dashboard preserves uncertain requests and blocks
navigation until their result is confirmed. No MCP write tool is added.

Stop older processes before applying migration 0033 and upgrading the API, MCP,
dashboard and backup service together. Existing 0032 sources and receipts are preserved.
Backups include imported text and import receipts; populated import state prevents a
lossy downgrade. The source mount and operator roots remain the deployment boundary.
