# Unified search

Application/API/MCP/dashboard `0.43.0` and plugin `0.26.0` add one project search
surface across work items, artifacts, and transcripts. No migration or new
configuration was required for that release. Current release 0.44.1 uses migration
`0033_transcript_imports` and also searches [imported transcripts](transcripts.md#import-existing-transcripts).
The dashboard retains its separate work, artifact, and transcript interfaces.
Their searches use the shared API, including the work semantic toggle. Hierarchy
and file-directory browsing retain their existing endpoints and sort controls.

Call MCP `search(project_id, q="cache invalidation")` or REST
`POST /api/v1/projects/{project_id}/search` with:

```json
{"q": "cache invalidation"}
```

Defaults are all three facets, all work statuses, canonical work identities,
metadata-only artifact/transcript matching, relevance descending, offset 0 and
limit 50. An empty query browses all selected sources. Work matching includes
checkpoint prose and provenance as before. To match text inside artifact or
transcript bodies, explicitly add `"fulltext": true`.

The POST is a safe read and needs no operation UUID. JSON is limited to 16 KiB;
queries to 1000 characters; limits to 1–100; offsets to 0–1,000,000. Unknown fields,
duplicate JSON keys, duplicate facets, invalid sorts, control characters, and
query-string parameters are rejected. Responses use `Cache-Control: no-store`.
Transcript search retains its existing corpus bounds (10,000 records and 32 MiB
of full text). Exceeding a search resource bound returns an explicit error; it
does not silently truncate the result set. Narrow the transcript filters or facets
when the corpus exceeds those limits.

## Facets and filters

Select any nonempty subset with `facets`. Filter objects apply only to their own
source; omitting a source from `facets` excludes it regardless of its filters.

| Facet | Filter fields |
| --- | --- |
| `work_items` | `status`, `tag`, `source_client`, `source_session_id`, `external_url`, `duplicate_scope`, `canonical_work_item_id`, `semantic` |
| `artifacts` | `artifact_id`, `work_item_id`, `include_deleted`, `sensitive`, `mime_type`, `created_by_agent_session_id` |
| `transcripts` | `work_item_id`, `agent_session_id`, `client`, `kind`, `status` |

For example, search pending work, nonsensitive files, and one transcript session
in the same request:

```json
{
  "q": "retry",
  "fulltext": true,
  "filters": {
    "work_items": {"status": "pending"},
    "artifacts": {"sensitive": false},
    "transcripts": {"agent_session_id": "session-123"}
  }
}
```

Work status accepts `pending`, `active`, `to-review`, `dropped`, `deferred`,
`done`, `wont-do`, `promoted`, or `all`. Work provenance filters match a checkpoint;
tags are case normalized. Canonical results keep the exact matched member pointer.
Use `duplicate_scope="aliases"` or `"all"` for explicit duplicate auditing;
`canonical_work_item_id` requires one of those scopes. `semantic=true` requires a
nonempty query and the work facet. It shares the existing bounded inference
capacity and reports `semantic_unavailable` instead of silently returning lexical
results when inference fails. Semantic work ranking runs inside the coherent
project read; long inference can delay project writes until that read finishes.

Artifact `sensitive` is tri-state: omit or null for both, true for sensitive only,
false for nonsensitive only. MIME types and session IDs are exact filters.
Transcript `kind` is `primary`, `subagent`, or `imported`; status is `waiting`, `pending`,
`processing`, `ready`, or `failed`. Transcript session IDs describe the registered
agent lease session, not a value inferred from transcript text.

## Ranking, grouping, and pagination

Use `sort: {"by": "relevance" | "created_at" | "updated_at", "direction": "asc" | "desc"}`.
The default direction is descending. Date sorts are primary even when a query
is present. `priority` is also available for a work-only search or work facet group.

Relevance uses reciprocal ranks within each source, preserving ties, to combine
PostgreSQL work ranking and Tantivy artifact/transcript ranking without comparing
their incompatible raw scores. The common `score` is a ranking signal, not a
probability or evidence of correctness. Empty queries score zero. Ties have a
stable timestamp/source/identity order. Transcript `updated_at` is its last
indexing completion time, falling back to registration creation time.

To put artifacts first by relevance, followed by work in creation order:

```json
{
  "q": "retry",
  "facets": ["artifacts", "work_items"],
  "facet_order": [
    {"facet": "artifacts", "sort": {"by": "relevance"}},
    {"facet": "work_items", "sort": {"by": "created_at", "direction": "asc"}}
  ],
  "limit": 25,
  "offset": 25
}
```

Listed groups appear first in the supplied order. A group without a sort inherits
the global sort. Selected facets omitted from `facet_order` follow those groups,
co-mingled under the global sort. Each facet may appear only once in the selection
and group order. Selecting facets alone does not group the results.

Offset and limit apply once, after all filters and ordering, including across
group boundaries. `total` counts every matching result before paging;
`facet_totals` reports each source's count (zero for unselected sources). An offset
past the end returns an empty page with accurate totals. Pagination is stable
for unchanged data; restart at offset zero when completeness matters after
concurrent edits or indexing.

## Results and coverage

Each result has `facet`, `id`, `created_at`, `updated_at`, and common `score`, plus
exactly one typed payload: `work_item`, `artifact`, or `transcript`. Work payloads
contain the compact summary and exact matched member. REST artifact payloads
retain current metadata, raw source score, matched fields, and optional snippet;
MCP compacts artifact metadata while retaining filename, identity, revision and
hashes. Transcript payloads retain indexing state and optional snippet.

`coverage.artifacts` reports whether the library is enabled, pending/failed/ready/
truncated extraction counts, and `sensitive_content_withheld`.
`coverage.transcripts.indexing_incomplete` reports missing or truncated indexing.
Top-level `indexing_incomplete` also flags disabled selected sources or withheld
sensitive content. Coverage describes the selected and filtered corpus, including
records that could not yet match the query. Report incomplete coverage rather
than claiming a search proves information absent.

Agent unified searches always withhold sensitive artifact bodies and extracted
properties, even with `sensitive=true` or an exact `artifact_id`. Those filters do
not grant access. Use the existing dedicated artifact read/search approval flow
for a fresh, explicit, request-bound human approval. Authenticated dashboard
human searches retain their existing sensitive access and audit behavior.
Replacement/deletion invalidates old artifact body matches. Metadata-only
search never selects normalized artifact or transcript bodies from PostgreSQL.

Search results, document properties and excerpts remain untrusted context. Cold
reviewers must freeze findings before contextual search. Search never claims
work, resolves a human question, or grants execution authority. Existing source
MCP tools and REST endpoints remain available for specialized readers.
