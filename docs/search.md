# Unified search

Application/API/MCP/dashboard `0.43.0` and plugin `0.26.0` add one project search
surface across work items, artifacts, and transcripts. No migration or new
configuration was required for that release. Current release 0.60.0 uses migration
`0041_artifact_passages` and also searches [imported transcripts](transcripts.md#import-existing-transcripts).
The dashboard retains its separate work, artifact, and transcript interfaces.
Their searches use the shared API, including the work semantic toggle. Hierarchy
and file-directory browsing retain their existing endpoints and sort controls.

Call MCP `search(project_id, q="cache invalidation")` or REST
`POST /api/v1/projects/{project_id}/search` with:

```json
{"q": "cache invalidation"}
```

Multi-term queries default to the work_items and artifacts facets. Agent transcripts
are omitted by default to avoid their corpus-loading and indexing cost. Explicitly
set `facets=["work_items", "artifacts", "transcripts"]` to include agent sessions,
or select `facets=["transcripts"]` for sessions alone. Blank and single-term queries
retain the default of all three facets. Transcript filters and facet ordering do
not implicitly opt in: include transcripts in `facets`. Other defaults are all
work statuses, canonical work identities,
metadata-only artifact/transcript matching, relevance descending, offset 0 and
limit 20 and `detail="compact"`. An empty query browses all selected sources. Work matching includes
checkpoint prose and provenance as before. To match text inside artifact or
transcript bodies, explicitly add `"fulltext": true`.

The POST is a safe read and needs no operation UUID. JSON is limited to 16 KiB;
queries to 1000 characters; limits to 1–100; offsets to 0–1,000,000. Unknown fields,
duplicate JSON keys, duplicate facets, invalid sorts, control characters, and
query-string parameters are rejected. Responses use `Cache-Control: no-store`.
Transcript search bounds metadata to 10,000 records and 32,000,000 bytes. Content
has a separate 512 MiB default budget, configured with
`MNEMONIC_TRANSCRIPT_SEARCH_MAX_BYTES` (1 byte–2 GiB). Cold builds stream bodies
into the configured transcript index; unchanged corpora are cached and snippets load only for the
global page. Metadata-only searches never fetch bodies or use the content budget.
Exceeding a bound returns an explicit error without partial results. Narrow the
transcript filters or raise the content budget with sufficient server memory.
`MNEMONIC_TRANSCRIPT_INDEX_DIR` selects the private disk directory and its API bind
mount in Compose. A matching cached corpus reopens after restart; the database
remains authoritative. See [transcript search deployment](transcripts.md#dashboard-settings-and-retrieval).

## Empty conjunctions and source scope

Default unquoted artifact and transcript queries require every normalized term to match
within one record, across metadata and (when opted in) content. Phrase and literal
intent use the explicit rules below; Boolean operators are not supported. Case and accents fold, punctuation
separates words, duplicate terms collapse, and overlong tokens are discarded by
the existing index analyzer. More than one resulting term selects the lighter
unified default above. Work retains its PostgreSQL lexical matching and optional
semantic ranking; a diagnostic work count uses its lexical matching rules.

Every unified response includes `search_scope.searched_facets`, a
`search_scope.transcripts` disposition (`searched`, `omitted_by_default`, or
`not_selected`), and `transcript_search_hint` explaining the explicit session-search
option. Disabled artifacts are absent from searched facets and remain visible in
coverage. Omitted sources do not make the selected sources' indexing incomplete;
they are explicitly outside the search's scope.

With default `diagnostics="on_empty"`, a complete query with zero matches before
pagination returns `term_diagnostics`, reporting document counts for each case-normalized term under the same filters and
`fulltext` setting. Diagnostic labels preserve accents: work lexical search can
distinguish `café` from `cafe`, while artifact/transcript matching folds both to
the same index term. Each source applies its own matching rules to the displayed term. For example, `q="FastAPI AsyncSession", fulltext=true` can return:

```json
{
  "total": 0,
  "search_scope": {
    "searched_facets": ["work_items", "artifacts"],
    "transcripts": "omitted_by_default",
    "transcript_search_hint": "Agent sessions can be searched by explicitly including \"transcripts\" in facets or calling search_transcript_contents."
  },
  "term_diagnostics": [
    {"term": "fastapi", "matches": {"work_items": 2, "artifacts": 1, "transcripts": null}},
    {"term": "asyncsession", "matches": {"work_items": 0, "artifacts": 0, "transcripts": null}}
  ]
}
```

This is an excerpt; ordinary page and coverage fields remain present. Null means
that source was not searched, while zero is a measured count in searchable data.
Counts are documents, not occurrences, and canonical work groups are counted once.
Every term can have matches while the conjunction has none because the terms occur
in different records. Pending, failed, truncated or withheld content still limits
what these counts establish. A fully ready index and a failed conjunction do not
prove the subject absent. Set `diagnostics="always"` to inspect term coverage on
positive results, including empty offset pages; `"off"` skips counts. Blank queries
return an empty diagnostic list in every mode.

Artifact/transcript diagnostics use the immutable search snapshot. Exact literal
matching uses scoped SQL; a count index is built only when diagnostics require it. Work diagnostics reuse its filtered corpus and canonical
selection. An omitted transcript source performs no transcript search or count.

## Facets and filters

Select any nonempty subset with `facets`. Filter objects apply only to their own
source; omitting a source from `facets` excludes it regardless of its filters.

| Facet | Filter fields |
| --- | --- |
| `work_items` | `status`, `tag`, `source_client`, `source_session_id`, `external_url`, `duplicate_scope`, `canonical_work_item_id`, `semantic`, `work_fields` |
| `artifacts` | `semantic`, `artifact_id`, `work_item_id`, `include_deleted`, `sensitive`, `mime_type`, `created_by_agent_session_id` |
| `transcripts` | `work_item_id`, `agent_session_id`, `client`, `kind`, `status`, `content_kinds` |

Each source also accepts inclusive `created_after` / `updated_after` and exclusive
`created_before` / `updated_before` bounds with a timezone. Unified `tag_counts`
returns paginated vocabulary for matching work before page slicing. See
[search exploration](search-exploration.md) for bounds, counts, and diagnostics.

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
group boundaries. `total` counts the complete eligible result population before paging, with
`total_kind` distinguishing lexical matches, semantic candidates, and browsing;
`facet_totals` reports each source's count (zero for unselected sources). An offset
past the end returns an empty page with accurate totals. Pagination is stable
for unchanged data; restart at offset zero when completeness matters after
concurrent edits or indexing.

## Results and coverage

Each unified result has `facet`, `id`, `created_at`, `updated_at`, and common `score`,
plus exactly one typed payload: `work_item`, `artifact`, or `transcript`.
`detail="compact"` is the default on all four discovery tools; `detail="full"`
retains complete summaries and metadata. The default page limit is 20.

Compact work rows retain identity, title, lifecycle and display state, priority,
updated time, canonical identity, ancestry and one-based rank within the work source.
A different `matched_member` is included only when alias text supplies the match.
`view="full"|"roots"` controls flat/hierarchy presentation independently of detail.
Artifact pointers retain identity, filename, revision, content availability and
relevant extraction/coverage flags with matching evidence. Transcript pointers
retain identity, client/session/work pointers, indexing flags and snippets.
Integrity hashes and full provenance remain in detail reads. Artifact search no
longer repeats upload-limit guidance.

Compact and full requests preserve identities, order, totals and coverage. Backend
hydration constructs only the returned page; semantic work ranking also avoids
building full summaries for every candidate. Source rank is an ordering signal,
not a relevance probability. Dashboard callers request full detail explicitly.

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

## Remediation summaries (0.47.0)

New remediation summaries include the finding count, the primary repository/file
(the location with the most findings; ties use submitted order), and one clause
per finding title. Long paths and clauses are abbreviated to the configured work
summary limit; the initial checkpoint retains every full finding. Search uses
this stored summary. Existing remediation summaries and permanent receipts retain
their authored history; this release has no backfill or schema migration.

## Effective search behavior (0.56.0)

Every unified and dedicated search response, including empty results, includes
`applied_filters`, `query_interpretation`, and `warnings`. The filter block names
the project and each actually searched source; null means the source was not
searched. Source filters include effective defaults and normalized tag spelling.
Work discovery now defaults to all statuses in both search front doors; pass
`status=pending` explicitly when that narrower view is intended. Ready-work
selection remains a separate read.

Query interpretation distinguishes PostgreSQL lexical matching, hybrid work ranking,
and artifact/transcript matching, including phrase and literal intent. It names the
selected fields and content inclusion; work uses null for `fulltext` because work
field selection is controlled by `work_fields`. Quoted phrases are honored and no
ignored-phrase warning is emitted. Disclosures apply before pagination, including
empty offset pages.

Compact discovery ships in 0.57.0. See the result contract above; full detail remains
an explicit option for selected records.

## Query intent and supporting evidence

`query_mode="terms"` is the default. Double-quoted spans require ordered adjacent
terms within one field; unquoted terms retain existing recall. `query_mode="phrase"`
treats the whole query as a phrase. Work uses PostgreSQL English stemming and
stopword positions; artifact/transcript phrases use the declared lowercase,
accent-folding tokenizer. `query_mode="literal"` requires a case-sensitive,
contiguous substring, preserving punctuation and internal whitespace in stored
normalized text. It does not compare original raw file bytes. Unclosed quotes,
empty phrases, and unsupported semantic work constraints return reviewed rules.

Work `work_fields` selects any nonempty subset of `title`, `summary`, `tags`,
`checkpoint`, `identifiers`, and `provenance`; all six are selected by default.
A work row or one individual checkpoint must satisfy the query. A conjunction
cannot be assembled across separate checkpoints. Work hits carry `evidence_mode`,
`matched_fields`, and at most three `excerpts`, totaling at most 320 text characters.
A field that proves the whole query supplies one excerpt; cross-field matches retain
multiple excerpts. Ordinary excerpts use at most 160 characters each; exact
phrases may use the full shared budget. Excerpts identify the matched member and
checkpoint where applicable. Semantic-only
hits are labeled without invented lexical evidence.

Transcript phrases and literals stay inside one canonical segment. Results pin
`normalized_revision`, `segment_id`, and `content_kind`; `matched_fields` separates
metadata from body evidence. Legacy bodies without segment boundaries are omitted
from exact content matching and counted in `unsegmented_content_omitted`; ordinary
term search retains legacy fallback. A qualifying span longer than the excerpt
budget retains its locator with `snippet_omission_reason="matched_span_exceeds_budget"`.
Term diagnostics explain individual lexical terms under the selected fields and
date bounds, even for phrase/literal requests; they do not count whole phrases.

## Scores, totals, and semantic availability

Search pages state `score_type` and `total_kind`; hits include their one-based
`rank` and score type. Ranks follow the requested ordering over the complete result
set before pagination. Unified wrapper ranks are global; nested source ranks are
within that source. Scores order results and are not relevance probabilities.
No universal cutoff or cross-source confidence scale is applied.

| Score type | Meaning |
| --- | --- |
| `none` | Browsing, with a zero or absent score |
| `postgresql_lexical` | Work relevance from the existing PostgreSQL ranking, including literal-filtered work |
| `tantivy_relevance` | Artifact or transcript native text relevance |
| `literal_presence` | Exact metadata/content presence, weighted 2/1; no lexical relevance inference |
| `hybrid_reciprocal_rank` | Existing work lexical/dense reciprocal-rank fusion |
| `unified_reciprocal_rank` | Source-normalized reciprocal rank used by the mixed result wrapper |
| `semantic_reciprocal_rank` | Rank of a source's semantic candidates |
| `cosine_similarity` | Raw embedding similarity, when supplied as component evidence |

`total_kind=lexical_matches` counts records satisfying textual matching;
`ranked_candidates` counts the eligible semantic population, including weak
neighbors; `browsed_records` counts a filter-only listing. Unified pages also use
`mixed` when their searched sources have different total meanings.
`facet_total_kinds` and `facet_score_types` retain each source's meaning; null marks
an unsearched source rather than an empty searched source.

The `semantic` block separates inference from coverage. `inference.status` is
`not_requested`, `completed`, or `unavailable`. Unavailable inference carries the
safe reason `capacity_exhausted`, `deadline_exceeded`, or `model_failure`.
`candidate_scope` independently names `none`, `full_scope`, or `lexical_shortlist`;
`partial_vectors` reports missing vectors within the semantic candidate set.
`comparison_incomplete` remains true for a shortlist, partial vectors, or a failed
semantic comparison. It does not classify a result as a duplicate or clear a
candidate for creation.

Duplicate suggestions retain their existing advisory lexical fallback. An
unavailable semantic comparison includes `retry={max_attempts:1,after_seconds:1}`;
a caller may retry once or continue saving work with the incomplete comparison
visible. Resource and deadline errors use the same bounded retry guidance and
`Retry-After: 1`. Search and duplicate suggestion share a bounded FIFO queue and model pool while
retaining their different candidate and cache composition policies. Admission is
per native call; defaults are two model workers, eight waiting calls, and a
five-second wait clipped to remaining request/stage time. Database and cache work
do not occupy a model slot. See [semantic inference](semantic-inference.md).

`semantic.cache_refresh` independently reports `not_needed`, `completed`, or
`failed`, with `reason=cache_refresh_failed` only for failure. Once coherent
ranking succeeds, failure to persist disposable embedding cache rows preserves
that ranking and the successful inference status. A later request can rebuild
the cache. Cache publication still uses its existing bounded lock waits and
version/digest checks.

Content-free logs record operation, phase, duration in milliseconds, and outcome
for request/inference queues, query embedding, candidate capture, document
inference, cache refresh, and total request handling. Vector-cache ready/missing
counts distinguish cold and warm work. Logs contain no query, transcript,
artifact body, provider exception text, or candidate identity. Existing admission
limits and deadlines remain unchanged; these timings diagnose contention before
changing capacity or ranking.

## Expanded retrieval (0.60.0)

Use [explicit project selection](multi-project-search.md) to search 1–10 projects
with one global page and per-project coverage. The existing single-project route
remains available. Artifact `semantic=true` searches current token-bounded body
passages, requires `fulltext=true`, and returns pinned evidence with embedding
coverage; see [semantic artifact search](artifact-semantic-search.md). This release
requires migration `0041_artifact_passages` and coordinated consumer upgrades.
