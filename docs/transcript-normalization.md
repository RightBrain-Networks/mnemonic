# Shared transcript representation

Mnemonic retains the verified native capture, then normalizes Claude Code and Codex
conversations into the same versioned representation before publishing searchable
text. Native files remain immutable in `MNEMONIC_TRANSCRIPT_DIR`; normalization is
structural and does not generate summaries or rewrite the native evidence.

## Durable format

`transcript_normalizations` stores an immutable revision manifest and
`transcript_segments` stores ordered blocks as individual rows. This is a streamable
representation: canonical JSON for each segment, sorted keys, compact separators,
ASCII JSON escapes, followed by a newline. `normalized_sha256` hashes those bytes;
`normalized_size_bytes` counts them. Database backups include manifests and segments.
The existing filesystem backup requirement for native captures remains unchanged.

Schema version and normalizer version are independent positive integers. The
revision is SHA-256 over canonical JSON of `[snapshot_id, source_sha256,
schema_version, normalizer_version]`. Event and segment IDs derive deterministically
from that revision and native record/block positions. Neither rebuild generation nor
worker execution identity contributes to these IDs. A new enrollment of an imported
transcript retains its public transcript ID but creates a new capture and revision.

Each segment records event ID, ordinal, native record ordinal and block position,
role, content kind, normalized text, native event/parent IDs when available,
timestamp when supplied, tool name/call identity, structured arguments or results
where provided, and the matching call segment when resolvable. Native record
ordinals count parsed records rather than physical lines, so blank JSONL lines do
not represent conversation events. Native source associations remain authoritative
in the existing transcript/work/lease tables.

Content kinds are `human_text`, `assistant_text`, `tool_call`, `tool_result`,
`system_text`, `reasoning`, `summary`, and `unsupported`. Role and content kind are
independent: Claude tool results can occur in user-role messages. Unsupported
binary/encrypted blocks have explicit dispositions and do not become searchable
text. Missing timestamps are explicit without inventing times. Missing call
references are reported as incomplete structural coverage. Codex completed UI
mirrors are omitted; durable Plan and FunctionCallOutput exceptions remain.

Native-source record/export and nesting limits remain enforced. The search text
extraction limit does **not** limit persisted canonical content. `truncated` reports
bounded search text; `normalization_incomplete` separately reports omitted or
unsupported structure. Canonical rows contain untrusted content, just like native
transcripts and derived text.

## Ingestion, rebuilds, and migration

Migration `0040_normalized_transcripts` queues existing transcripts through the
current durable job reconciler. Already-ready text stays usable while backfill
runs. Paused projects and active work lease generations retain the existing guards.
No synchronous parsing is added to import requests.

Normalization and indexing use the existing job ownership and generation checks.
Publication also verifies capture snapshot and copied source hash. Manifest and
segment insertion is atomic. Successful normalization can be staged when text
extraction fails; an existing searchable text snapshot keeps its original active
normalized revision until replacement text publishes. Initial normalization can
become readable while text extraction is pending. A stale worker cannot attach a
former capture's normalized revision to an enrolled transcript.

Rebuilds reuse persisted normalization when snapshot, source hash and versions
match. They stream segment text under the extraction budget without loading tool
payloads or rerunning native adapters. A normalizer/schema version change selects
a new revision generated from the retained native copy. Interrupted work resumes
through existing expiry/retry/rebuild controls; a normalization error preserves
prior ready search text and reports incomplete coverage.

Normalized revisions are retained with their transcript. Redundant imported rows
removed during a project move remove their derived normalization rows too, following
the existing import deduplication policy. Enrolled transcript provenance is retained.

## Retrieval and search

Existing hashes retain their meanings: `sha256` is the native source bytes,
`text_sha256` is downloaded/indexed UTF-8 text, and `normalized_sha256` is the common
structured representation. Existing flat `/text` offsets and `/content` downloads
continue to address text pinned by `text_sha256`.

For structured context, call the existing text endpoint with `segment_id` and
`expected_normalized_revision`. Optional `before` and `after` select up to 20
surrounding blocks in total. `offset` addresses characters within the selected
block and supports values through 1,073,741,824, independently of the search
extraction cap. `limit` is 1–200,000 and bounds returned text plus retained payload
bytes. The response has typed `segments` with generated 24-hex-character identities,
zero-based ordinals, one-based source record numbers, content kinds, native metadata,
text fragments and their `text_offset`/`text_truncated` state. Native optional fields
are nullable. Payloads retain their JSON types when the read budget permits.

`segment_window` echoes the initial request's `anchor_segment_id`, `anchor_ordinal`,
`first_ordinal` and `last_ordinal`. The echoed range includes the anchor even if a
large preceding block consumes the response budget before the anchor is returned.
All returned segments belong to the top-level 64-hex `normalized_revision`; the
flat reader returns `segments=null` and `segment_window=null`.

Generated fields are bounded and always present. Optional native strings share a
4,096-byte serialized metadata allowance across the whole response window. When a
segment's native metadata group would exceed the remaining allowance, its optional
fields are null with `metadata_omitted_for_budget`. PostgreSQL checks the size before
returning that projection, so oversized native IDs and tool names are not hydrated
merely to discard them. Large tool payloads are likewise checked before retrieval
and carry `payload_omitted_for_budget` when omitted. These omissions change only the
bounded read projection; canonical stored segments and immutable native bytes remain
exact. Public native strings cannot exceed 4,096 characters; generated source-block
paths are at most 64 characters, and disposition lists contain at most 16 entries
of up to 80 characters each.

`next_segment_id`, `next_segment_offset`, and `next_segment_after` continue a response
cut short by its budget. Send that locator and offset with `before=0` and
`after=next_segment_after`; the next response echoes this continuation's window.
This walks the original range even when a large preceding block consumes the first
response. A nonzero offset with nonzero before is rejected. Reconstruct fragments
by segment ID and `text_offset`, order completed segments by ordinal, then join with
`\n\n`. Joining page `text` values alone would omit separators across page boundaries.
Empty segments still advance the continuation and retain their position.

`total_chars` describes the logical selected joined text, including two characters
between adjacent segments and excluding the starting offset. It describes the
current request's remaining window, not only returned fragments. The returned
`text` is the joined fragments in this response and never exceeds `limit`.
Unknown segments return 404; a missing/stale revision pin returns a conflict.

Dedicated and unified transcript search accept optional `content_kinds` alongside
`fulltext=true`. Ordinary all-terms matching retains document-level recall across
eligible blocks. Supporting snippets return a `segment_id`, `content_kind`, and
`normalized_revision` to open directly. Metadata matching remains available with a
content-kind filter, but candidates must contain at least one selected kind in the
published revision, including when browsing with a blank query. Unnormalized legacy
text has no invented role classification or segment boundaries and contributes no
body to a content-kind-filtered search. Coverage reports that omission.

When restoring a database backup, Mnemonic verifies segment order, content/hash
consistency, manifest identity, and the active revision's captured source witness.
Restore retains structured revisions and advances only transient job generations.
