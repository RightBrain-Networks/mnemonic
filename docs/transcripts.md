# Agent transcript indexing

Release **0.52.1** adds [audited operator path recovery](transcript-recovery.md)
for incorrect historical assertions. Migration `0038_transcript_recovery`
preserves the reported path and records each approved replacement independently.
Plugin **0.30.1** and portable skills require verified native file paths and warn
against guessed worktree paths, workflow directories, and temporary task output.

Release **0.52.0** copies transcripts into a private durable bind before indexing.
RabbitMQ delivers copy, index, and backup jobs to the shared worker. Existing
records are backfilled automatically, and rebuilds reuse retained raw bytes.
Read [the deployment and migration guide](transcript-jobs.md) before upgrading
to migrations `0036_transcript_copies` and `0037_background_jobs`.

Release **0.50.0** adds OpenAI Codex primary and subagent rollout JSONL, mixed-client
folder imports, and optional private Codex source mounts. No database migration is
needed for that release; its migration head was `0035_prompt_library`. Existing Claude sources
and import receipts retain their identities.

Application/API/MCP/dashboard 0.44.0 and migration `0033_transcript_imports` add
workspace imports for existing Claude Code transcripts. Release 0.44.1 fixes Docker
access to private host transcripts with a configurable API image identity. Plugin
remains 0.26.0 and migration head remains `0033_transcript_imports`. Release 0.44.2
fixes content search for large imported libraries. Release 0.45.0 adds a private
configurable disk index, including reuse after restart. Release 0.45.1 makes the
source mount part of base Compose and derives its default allowlist from the
configured source, fixing startup with explicit base/TLS commands. No migration
or reindex is required.

Mnemonic indexes agent session transcripts once the associated work lease ends.
An MCP claim records an explicit primary transcript location or null. Every fresh
completion, retirement, merge, deletion, or review completion reports additional
subagent locations or explicitly asserts null. `release_claim` also accepts known subagent
locations when pausing. Release, expiry, and terminal lifecycle transitions make
waiting transcripts eligible for copying and indexing. Files are snapshots at copy time;
there is no continuous tailing while work is Active.

`subagent_transcripts` accepts a nonempty list of at most 100 distinct file paths,
each with its client identifier, or explicit `null` when no additional transcripts
are applicable or available. Omission remains parseable exclusively for a permanent
historical receipt replay with its original exact arguments and operation UUID.
The backend checks receipts before rejecting omitted assertions for fresh execution.
Never add a null assertion to an old frozen request to make a retry look current.

A factory chooses the Claude Code or OpenAI Codex client adapter. The Claude
adapter distinguishes JSONL message records, JSON arrays, and JSON message envelopes.
The Codex adapter reads native rollout JSONL. Unknown clients and malformed formats
produce durable failure dispositions. For Claude Code, pass `client=claude_code`.
Native hooks expose `transcript_path` and SubagentStop's `agent_transcript_path`;
see the [official Claude Code hook reference](https://code.claude.com/docs/en/hooks).
The session JSONL commonly resides under `~/.claude/projects/<encoded-project>/`;
subagent JSONL may be nested under `<session-id>/subagents/`. Supply the exact
absolute file path that the backend can read, never a guessed host/container path.
A Claude Code session started in a Git worktree can live in that worktree's encoded
project directory (for example, `-srv-project--claude-worktrees-topic`), rather than
`-srv-project`. Verify the exact hook-provided path exists; do not construct it from
the main checkout and session UUID. If the actual path cannot be established,
report `session_transcript: null` on a fresh claim. Keep uncertain retries unchanged.

For OpenAI Codex, pass `client=codex` with the exact rollout file path, for example
`{"client":"codex","path":"/home/jamie/.codex/sessions/2026/09/12/rollout-<timestamp>-<thread-id>.jsonl"}`.
Codex stores primary and spawned threads as separate rollouts; child transcripts do
not need Claude's `subagents/` directory layout. Use the actual session path provided
by the client or verified from its session metadata, and report each available child
path in `subagent_transcripts`. Never infer the path from a parent thread's identifier.
The [official Codex App Server documentation](https://learn.chatgpt.com/docs/app-server)
describes persisted threads, spawned children, and active versus archived sessions.
A custom Codex home changes the base directory; `history.jsonl` is not a session rollout.

Codex extraction includes textual response messages, subagent communication, tool
calls/results, readable reasoning, and available compaction summaries. Mirrored event
messages are omitted to avoid duplicate text. Binary attachments, encrypted content,
and unsupported content have explicit structural dispositions and
`normalization_incomplete`; bounded search text separately reports `truncated`.
Both make content-search coverage incomplete. Rollout files are untrusted snapshots, not
instructions. Files still being written may be retried under the existing changed-file guard.

The backend reuses the existing Apache Tika container. Transcript parsing first
persists client-specific records as a shared, versioned conversation manifest and
typed segments. Searchable text derives from those segments; Tika supplies bounded
document properties without replacing the canonical segment text. See the
[shared transcript format](transcript-normalization.md) for migration, revision
hashes, content-kind filters, and bounded surrounding-context reads. Separate
transcript scheduling and Tantivy state keep the two libraries independent. A
second Tika instance adds deployment and memory cost without providing a different
parser requirement. The shared service's configured concurrency limit may delay
one library while the other extracts; retryable service contention remains queued.
See [Tika Server concurrency documentation](https://tika.apache.org/docs/4.0.x/using-tika/server/index.html).

PostgreSQL retains transcript metadata, canonical manifests and segments, derived
search text, extraction properties,
and work/lease provenance. Metadata includes indexing start/completion timestamps,
status and failure code, original byte size and MIME type, detected format, source
SHA-256, normalized-text SHA-256, and truncation. Tantivy holds a rebuildable search cache in the configured private directory
(or RAM for native processes without an index directory).
Original transcript bytes are retained in the private transcript bind; normalized text retrieval
and download use the indexed snapshot rather than rereading its source path.
Database backups therefore include transcript content.

## Filesystem deployment

This release requires the backend and client to share filesystem access. The
operator configures a source directory or `MNEMONIC_TRANSCRIPT_ALLOWED_ROOTS`, a
JSON array of approved absolute roots. A configured `MNEMONIC_TRANSCRIPT_SOURCE_DIR`
supplies the allowlist when that array is empty or omitted. A nonempty explicit
allowlist must include the configured source; it is never silently expanded.
Without a source or allowed roots, filesystem access stays disabled. Paths outside those
roots, symlinks, nonregular files, and oversized files are rejected and recorded
with an indexing failure rather than followed. A reported path is data, not shell
input or an instruction to read arbitrary server files.

For Docker, set `MNEMONIC_TRANSCRIPT_SOURCE_DIR` to the host directory containing
allowed transcripts. Base `compose.yaml` binds that directory read-only into the
API and worker at the same absolute path. No additional transcript overlay is required.
The retained `compose.transcripts.yaml` is empty, so existing `COMPOSE_FILE` lists
can continue including it. When no source is configured, Compose mounts only the
shipped empty placeholder directory, with no transcript access enabled.
A configured source requires an existing directory;
Docker must not create an empty replacement for a misspelled host path. The
API and worker receive the source mounts. Tika receives normalized text over HTTP.

To expose Codex alongside Claude, configure either or both optional directories:

```dotenv
MNEMONIC_CODEX_TRANSCRIPT_SOURCE_DIR=/home/jamie/.codex/sessions
MNEMONIC_CODEX_ARCHIVED_TRANSCRIPT_SOURCE_DIR=/home/jamie/.codex/archived_sessions
```

Base Compose mounts each configured directory read-only at its original absolute
path. Omit unavailable directories. Keep `MNEMONIC_TRANSCRIPT_SOURCE_DIR` set for
an existing Claude library. An empty or omitted `MNEMONIC_TRANSCRIPT_ALLOWED_ROOTS`
defaults to all configured sources; a nonempty explicit allowlist must include each
configured source. Restart the API after changing mounts. Do not mount the entire
`.codex` directory: it also contains credentials and configuration. Matching the API
UID/GID to the owner applies to private Codex files as well.

Claude Code can create owner-only (`0600`) files. The API image defaults to UID/GID
10001, which cannot read files owned by a different host user. Set the build
arguments `MNEMONIC_API_UID` and `MNEMONIC_API_GID` to that user's numeric IDs.
The API still runs as the unprivileged `mnemonic` account. Matching the UID handles
both existing files and newly created private files without changing transcript
permissions. A supplementary group or default ACL alone cannot grant access when
the client explicitly creates files with mode `0600`.

For example, first check the directory owner with
`stat -c '%u:%g' /home/jamie/.claude/projects`. For this host's UID/GID 1026:1000,
add these values to the private `.env`:

```dotenv
MNEMONIC_TRANSCRIPT_SOURCE_DIR=/home/jamie/.claude/projects
MNEMONIC_API_UID=1026
MNEMONIC_API_GID=1000
COMPOSE_FILE=compose.yaml:compose.tls.yaml
```

Omit `compose.tls.yaml` if the deployment does not use TLS; retain any other
required overlays. Both ordinary `docker compose up -d --wait` and explicit
`docker compose -f compose.yaml -f compose.tls.yaml up -d --wait` preserve the
source mount. Explicit `-f` still overrides `COMPOSE_FILE` for other overlays.
The example IDs are host-specific; check ownership before using them on another
machine. Native deployments can set `MNEMONIC_TRANSCRIPT_SOURCE_DIR` or configure
`MNEMONIC_TRANSCRIPT_ALLOWED_ROOTS` directly, under an identity that can read the
approved files.

Changing the API UID also requires changing ownership of its **artifact directory
and existing contents**. Artifact storage checks that each directory and file
belongs to the effective API user. Build images first, then stop API writers before
changing ownership. For the default artifact directory and example IDs:

```sh
docker compose build api
docker compose stop api
sudo chown -hR -- 1026:1000 ./artifacts
docker compose up -d --wait api
```

Use the configured `MNEMONIC_ARTIFACT_DIR` if different. Keep private artifact modes
(`0700` directories, `0600` files); a fresh installation can use
`sudo install -d -m 0700 -o 1026 -g 1000 ./artifacts`. Backup storage now uses the
shared worker UID/GID; migrate ownership from the old service's 10001 if needed.
A coordinated application/schema upgrade requires stopping older API/MCP/dashboard/backup processes before
migration, as described below; the commands above cover an identity-only change.

Verify the live configuration without printing credentials:

```sh
docker compose exec -T api id
docker compose exec -T api python -c 'from mnemonic_api.config import Settings; print(Settings().transcript_allowed_roots)'
docker compose exec -T api python -c 'import os; p="/home/jamie/.claude/projects/<project>/<session>.jsonl"; fd=os.open(p, os.O_RDONLY | os.O_NOFOLLOW); os.close(fd); print("Transcript is readable")'
```

Replace the final example with an existing transcript path. Opening and closing the
file verifies permissions without displaying its body. A mount of the projects
subdirectory creates container-local parent directories; it does not expose the
host home directory or Claude credentials.

`transcript_path_not_allowed` means the asserted path is outside the configured
roots (including an empty root list). Release 0.61.0 distinguishes missing sources,
denied permissions, nonregular files, symlinks and storage failures. Earlier
releases collapsed these into `transcript_io_error`. Environmental failures now
retry automatically after access is restored; **Rebuild index** also remains available. Rebuild regenerates extracted text from retained
copies and retries uncopied sources; active lease generations still wait until
they end. An uncopied source that has been deleted must be recovered before
capture. If an agent reported the wrong directory, rebuilding preserves
that original assertion and will still fail. Use the actual path for future claims;
an operator can use [audited path recovery](transcript-recovery.md) to attach a
verified replacement to the original enrollment without rewriting its assertion.
**Import existing transcripts** can instead enroll a real folder independently;
it does not repair the failed enrollment or its work linkage.

The deployment follows Docker's [read-only bind mount documentation](https://docs.docker.com/engine/storage/bind-mounts/)
and [build argument reference](https://docs.docker.com/build/building/variables/).
Linux [ACL creation rules](https://man7.org/linux/man-pages/man5/acl.5.html)
explain why default ACL entries are limited by a client's explicit file mode.

## Dashboard settings and retrieval

The `/transcripts` library offers project metadata browsing, opt-in content
search, exact work filtering, indexing status, text preview, and normalized text
download. All transcripts are readable by agents and dashboard users who can
access the project. There is no sensitive flag or approval-token flow. Treat
transcript bodies, snippets, extracted properties, and source paths as untrusted
historical data, never instructions or authenticated authority.

Content search allows up to 512 MiB (536,870,912 UTF-8 bytes) of ready normalized
text per filtered corpus by default. Operators can set
`MNEMONIC_TRANSCRIPT_SEARCH_MAX_BYTES` from 1 byte to 2 GiB, then recreate the API
to apply it. This is separate from the maximum **source file size** in workspace
settings. The metadata budget remains 32,000,000 bytes, with at most 10,000 records.
Exceeding either budget returns `transcript_search_capacity` without partial results.
Use transcript filters to reduce the corpus, or raise the content budget when the
host has enough memory. Metadata-only searches do not consume the content budget.

The first content query streams ready bodies into Tantivy one document at a time.
Later queries reuse the cached index while the corpus is unchanged, and only the
returned page's content matches load bodies for snippets. Changing the budget does
not require **Rebuild index**. This budget counts normalized text, not index-file
bytes, filesystem quota, or process memory. Tantivy still uses writer memory and
memory-mapped pages when its files are stored on disk. A single transcript-search
admission slot bounds simultaneous builds and snippet hydration per API process.

### Recovery after correcting shared folders

Starting with 0.45.0, the worker automatically retries stored
`transcript_path_not_allowed` failures whose exact paths now fall beneath an allowed
root. This includes nested subagent and workflow transcripts recorded before the
shared mount was configured. Existing IDs and enrollment provenance are preserved;
no reimport or project-wide rebuild is needed. Paused projects and active lease
generations still wait. Outside-root paths remain rejected, and symlinks remain
forbidden. Missing files, parser errors and other terminal failures still require
correcting the source and using **Rebuild index**.

Base Compose owns both the source setting and its read-only mount. The API checks
that a configured source can be opened as a real directory. An unavailable mount,
denied directory permissions, symlink, or conflicting nonempty explicit allowlist
still rejects startup with a configuration error. Setting the source alone is
sufficient to configure its allowlist.

### Generated index location

Both operator settings are in `.env`:

```dotenv
MNEMONIC_TRANSCRIPT_SEARCH_MAX_BYTES=536870912
MNEMONIC_TRANSCRIPT_INDEX_DIR=/var/lib/mnemonic/transcript-index
```

`MNEMONIC_TRANSCRIPT_INDEX_DIR` is an absolute, dedicated directory for the generated
search index. Base Compose automatically mounts the same host path read-write into
the API; no index overlay is needed. Only the API receives this mount. The transcript
**source** directory is separate and retains its read-only mount and allowed roots.

Before starting or upgrading Compose, create the chosen directory privately for the
API UID/GID. For the default service identity:

```sh
sudo install -d -m 0700 -o 10001 -g 10001 /var/lib/mnemonic/transcript-index
docker compose up -d --wait api
```

For this installation's API UID/GID 1026:1000, a project-local location can instead be
configured as `MNEMONIC_TRANSCRIPT_INDEX_DIR=/srv/mnemonic/transcript-index` and
created with `sudo install -d -m 0700 -o 1026 -g 1000 /srv/mnemonic/transcript-index`.
After changing either setting, recreate the API. A new empty location builds its
cache automatically from PostgreSQL on the next search. Choose a dedicated folder;
the API rejects unrelated contents, public permissions, a symlink at the root, or
an owner mismatch. Native deployments also require ancestor directories owned by
root or the API user and protected against other users renaming their children
(sticky directories such as `/tmp` are accepted). Container parents created around
the bind mount meet that rule. Changing the API UID also requires migrating index
ownership.

The private snapshot contains indexed terms and positions derived from transcripts.
Do not share or commit it. It is disposable and excluded from project backups; the
authoritative text remains in PostgreSQL and its backups. A completed cache survives
API restart and is reused only when its corpus fingerprint and engine/schema version
match. Changed, incomplete or corrupt index snapshots are rebuilt. **Rebuild index**
clears the cached snapshot while retaining the selected storage directory. Old open
searchers keep their coherent snapshot while a new one is built.

One API process exclusively locks each index directory. Separate API processes or
replicas need separate directories. Missing mounts, invalid permissions, or another
owner of the lock cause `transcript_index_unavailable`; there is no silent fallback
to a different directory. Native Linux deployments set the same environment variable;
unset or empty retains the previous RAM cache. Compose defaults to the disk path
shown above, including when its variable is absent or empty.

See the [Tantivy directory and reader API](https://tantivy-py.readthedocs.io/en/latest/api/tantivy/tantivy.html#index)
for the underlying disk-index and immutable-searcher support.

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
It identifies native Codex record signatures from bounded file headers and routes
those sources to the Codex adapter. Claude and Codex sources can share an import
folder; client detection does not depend on filenames. To import Codex history,
select the configured `sessions` or `archived_sessions` directory, or a smaller
dated subfolder. Unknown or malformed JSONL keeps a durable indexing failure.
Imported sources are detected again from the current bytes during extraction,
so fixing permissions and rebuilding can recover an earlier unreadable or
misidentified source without changing its ID or import receipt. Explicit
agent-reported clients remain authoritative for enrolled sources.
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
When enrolled work moves into a project that already imported its source, the move
atomically removes only the redundant imported record. The enrolled ID, snapshot,
and work/lease history survive; import receipts retain their original counts.
The removed imported ID subsequently returns 404; no redirect is created.

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

## Access diagnostics and resilient recovery (0.61.0)

See the [transcript reliability RCA](transcript-reliability-rca.md) for the observed
production causes and coordinated upgrade procedure. `/transcripts` now shows
project-wide warnings above search, including the exact path, needed permissions,
actual worker identity, storage problems, mismatched API/worker settings and stale
worker reports. Search filters do not hide these warnings. A failed health request
also remains visible. Diagnostics contain metadata, never transcript bodies.

Fresh claims and closeouts now verify readable native `.jsonl`/`.json` files under
approved roots before accepting assertions. Invalid assertions reject the fresh
transaction; use explicit null if the actual file cannot be established. Existing
successful receipt replays remain independent of current filesystem access.
Historical bad paths still require audited recovery rather than a different retry.

Missing files, denied permissions, full/read-only storage and earlier generic I/O
failures are rechecked every five minutes. No rebuild is needed after access to the
same file is restored. Active leases and paused projects still wait. Parser and
integrity failures retain their own explicit dispositions. Configured source outages
allow the dashboard to start so operators can see the warning; inconsistent explicit
allowlists remain configuration errors.

For a new local installation, choose source roots explicitly (omit absent roots):

```sh
python scripts/setup.py --transcript-source /home/your-user/.claude/projects \
  --codex-source /home/your-user/.codex/sessions
```

Setup selects the chosen source owner's numeric UID/GID for both service images
and prints matching private-storage creation commands. Existing `.env` files are
left unchanged. Use `--service-uid` and `--service-gid` for an explicit identity;
multiple owners need an operator-managed access arrangement. A default ACL or
supplementary group does not override a client's explicit creation of mode 0600
files. Do not make transcripts world-readable to accommodate a mismatched container.

After configuring mounts/storage and building the images, verify access before
startup, then verify running services:

```sh
python scripts/check_transcript_access.py --one-off
python scripts/check_transcript_access.py
```

The checker uses each service's own settings, mounts and unprivileged identity.
It opens/closes a bounded sample (default 100 files, at most 1,000), reports incomplete
sampling, and exercises worker storage. It does not change source permissions or
print credentials/content. Run the check again after moving sources, changing users,
restoring backups or altering Docker mounts. Owner-only files from another user,
SELinux/ACL restrictions, rootless UID mappings and Docker Desktop file sharing may
need host-specific intervention; use the actual in-container probe as the evidence.
A remote Docker daemon cannot mount a path from the client computer without an
explicit filesystem share. Both services need the same exact absolute source paths.


## Verified relocation after enrollment (0.62.0)

Claude Code can move a session's transcript when entering or leaving a worktree;
Codex sessions may also move to an approved archive root. A valid claim-time path
can disappear before the lease ends. See [Claude Code's worktree resume behavior](https://code.claude.com/docs/en/worktrees#resume-a-worktree-session).

For new assertions, the API now hashes the first at most 65,536 bytes through its
verified no-follow file descriptor. It stores only the hash, byte count, filename
and version. A file shorter than 256 bytes has no relocation evidence. This bounded
server read never returns transcript content to the claiming agent; agent-side
path verification still needs only filesystem metadata. Existing records are not
backfilled with evidence inferred after enrollment.

The worker first reuses an already-published private snapshot. Otherwise it checks
the original path and prefix. If that file has moved, changed identity, or falls
outside corrected roots, a bounded scan searches only current approved roots for
the exact filename. Only one verified matching regular file, after a complete scan,
is eligible. Overlapping roots are deduplicated, links are never followed, and the
prefix is rechecked on the same descriptor that streams the private copy. Source
appends are allowed; prefix replacement is not. The original `source_path` remains
unchanged; `copy_source_path` records the actual read location when known. A crash
that preserves bytes but loses the source-location observation leaves it unknown.

Scans stop at 100,000 entries, 10,000 directories, depth 64 or five seconds checked
between filesystem operations. Slow or unavailable filesystems can still delay an
individual OS call. A limit, unreadable directory or concurrent disappearance
prevents selection from an incomplete scan. Ambiguous matches and changed identity
have explicit warnings at the top of `/transcripts` and are rechecked every five
minutes. Use dedicated source roots or audited recovery when a complete scan is
impractical. Never expand roots to include unrelated files or temporary stdout.

Lease and pause guards still apply. Permanent receipts replay unchanged even when
the original path has moved. Operator recovery keeps its stronger full-file hash
and size pin; automatic relocation never relaxes that approval. If the approved
file changes, wait for writes to finish and prepare a new recovery. Rebuilding
cannot choose a replacement or rewrite a historical assertion.

Migration `0043_transcript_source_identity` adds nullable enrollment evidence and
capture provenance, shape constraints and a guard against changing enrolled
identity evidence (including adding it to old unproven records). Import-to-session
enrollment establishes fresh evidence. Project backups retain both new columns;
restores fence jobs as before. Stop old processes for the coordinated upgrade.
A downgrade refuses to discard captured enrollment evidence.


## Session metadata and work navigation (0.63.0)

The dashboard **Last Updated** column and transcript `updated_after` / `updated_before`
filters use the latest valid timestamp in the retained native conversation. A verified
source-file modification time is the fallback when no native timestamp exists; unknown
historical times stay null. Sorting and date filters use enrollment time only for records
whose activity time is unknown, while the activity field still reports null. Rebuilding
the search index does not advance session activity.
`index_created_at` identifies the current successful index build, not session completion.
These times are stored in transcript metadata alongside native session IDs, models,
message counts and parent-session references when supplied by the client. `session_id`
remains the exact original reporting-session provenance; `session_ids` contains native
identities, which can differ for subagents and are also available for imported sessions.

Work detail and context return `transcripts={items,total,omitted_count}` with at most
20 metadata-only links. Follow a link using `get_transcript(project_id, transcript_id)`;
page additional links with `search_transcripts_content(work_item_id=...)`. Each transcript
retains its originating work ID and current project ID for `get_work`. The dashboard
links in both directions and can open a specific transcript from a work item.

Transcript/work-only unified search uses a read-only repeatable-read snapshot rather
than a project write lock. Artifact searches retain sensitivity serialization and audit
behavior. Search congestion is reported as a search failure with instructions to retry
that read; it never requires an operation ID. Actual writes still require identical
same-operation retries when their outcome is uncertain.


### Search failure root cause and upgrade verification

Before 0.63.0, unified search entered the receipt-protected project mutation scope.
It held a project row write lock through corpus loading, index construction, ranking,
and result hydration. The scope allowed a 120-second search, but another request
could wait only two seconds for its project lock. Corpus loading can exceed that wait,
so overlapping searches or project writes could fail normally and be mislabeled as unavailable
"client operation safety", even though a search has no operation ID or write receipt.
A regression reproduces the cause by holding the project row in an independent
writer transaction while searching transcripts. Repairing or deleting operation
receipts would not address this lock contention.

Transcript/work-only search now uses one read-only repeatable-read snapshot with
bounded server deadlines. It does not acquire project row locks. Artifact-inclusive
search keeps its existing sensitivity and audit serialization; contention, exhausted
deadlines and lost database connections return `search_temporarily_unavailable` with
instructions to search again. The dashboard separately retries short-lived
`transcript_search_busy` index admission failures with bounded, cancellable backoff.
It does not automatically repeat writes or potentially expensive timed-out searches.
Actual uncertain writes retain permanent receipt protection and now explain how to
retry the pending action without duplicating it.

Migration `0044_transcript_metadata` derives timestamps only from retained current
normalized segments and records the current successful index creation time. It does
not infer historical source-file modification times. Migration regression tests and a
private upgrade rehearsal verify that source assertions, retained-content hashes, snapshot IDs, transcript statuses, leases,
receipt rows and background jobs remain unchanged. The fresh-install migration chain
and project backup schema catalog also include the new nullable timestamp columns. Upgrade API, worker, MCP and dashboard
together, with old writers stopped during the migration.

Regression coverage includes a transcript search during an independently held project
write lock, artifact sensitivity lock contention, real PostgreSQL statement timeout
and disconnected-session recovery, native timestamps beyond the searchable prefix,
index rebuilds, unavailable native timestamps, project moves, bounded work links,
and browser retry/navigation on desktop and narrow screens for both native formats.
