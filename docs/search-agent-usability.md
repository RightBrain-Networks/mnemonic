# Search usability and independent duplicate vectors

Application/API/MCP/dashboard 0.76.0 keeps the 57-tool catalog and Claude plugin
0.44.0. Alembic `0049_duplicate_embeddings` separates the work-search and
duplicate-suggestion vector caches and adds background duplicate refresh delivery.

## Query discovery

All four MCP search tools accept `q` or `query`, with a common 1,000-character
limit. Supply at most one spelling. Work and unified searches can omit both for
browsing; artifact and transcript content searches require one nonempty query.
Existing REST `q` names remain valid, and the aligned REST query limits are also
1,000 characters.

Semantic retrieval remains opt-in. Unified work and artifact semantic filters
remain independent; work semantic requires all work fields and unconstrained
terms, while artifact semantic also requires `fulltext=true`. Status defaults,
ranking weights, total meanings and scope disclosure are unchanged.

Every advertised MCP tool description fits within 2,048 characters. Search
preconditions and sensitive-content approval rules appear before longer detail.
`help({"topic":"search details"})` provides bounded complete-guidance pages with
explicit continuation links. Field help and semantic JSON Schema descriptions
explain what a parameter does, its prerequisites, cost and coverage signals.
The `get_work` description names the actual `search_transcript_contents` tool.

## Bounded result pages

All four search routes cap the result payload at 32,768 bytes. The budget counts
indented, ASCII-escaped JSON conservatively; HTTP JSON is smaller, and JSON-RPC
framing is outside this result-payload budget. The server retains the longest
fitting prefix of the requested page. Requested `limit`, ranking, identities,
`total`, facet totals, scope and coverage remain intact.

Follow `next_offset` until it is null. Do not advance by the requested limit:
`page_truncated=true` means the byte budget shortened this page. Concurrent data
changes retain the existing offset-pagination caveat. If even one item or the
coverage envelope cannot fit, `413 search_result_too_large` recommends
`detail=compact`, `diagnostics=off`, a smaller `tag_counts.limit`, or selected-record
detail tools. An oversized item is never silently skipped.

Compact work results keep canonical identity, project identity, lifecycle,
search/display state, per-hit ranking signals and evidence provenance. Only empty
ancestry and false truncation flags are omitted, with their defaults restored by
readers. An unrequested semantic block is omitted; requested inference and
coverage remain explicit. Sensitive-content warnings, withholding counts and
incomplete coverage remain present. A filter never grants sensitive-content
access.

## Duplicate cache refresh

Vector rows now use `(work_item_id, purpose)`, with separate `work_search` and
`duplicate_suggestions` purposes. Their text compositions and configuration/digest
checks remain independent. Alternating work search and duplicate checking no
longer overwrites the other consumer's cache.

The duplicate API performs no document-vector inference for stored work items.
Caller-supplied external candidates retain bounded request-time inference.
Warm complete vectors
still return synchronous semantic ranking with `candidate_scope=full_scope`.
Missing vectors create bounded background demand and return available lexical or
cached-vector evidence. With no usable semantic vectors, the result reports
`inference={status:unavailable,reason:vectors_pending}` and
`cache_refresh.status=queued`. Cached subsets may produce completed inference
with `candidate_scope=lexical_shortlist`, `partial_vectors=true` and incomplete
comparison. A completed shortlist remains incomplete even if every shortlisted
vector is current: it does not establish full-project comparison.

`duplicate_embed` uses the existing PostgreSQL ledger and worker. Each delivery
processes at most 16 documents and commits that completed batch before the next
delivery. Native inference capacity remains held until the actual call finishes;
a response deadline cannot safely free a still-running native call. Work search
retains its request-path refresh behavior, with consumer-separated storage.

Only `capacity_exhausted` offers one immediate retry after one second.
`vectors_pending`, `deadline_exceeded` and `model_failure` have `retry=null`.
Queued cache refresh is not a reason to repeat the same expensive call
immediately. Failed refresh enrollment or exhausted background delivery reports
`cache_refresh_failed`; repeated reads do not reset an exhausted generation's
retry budget. Source changes and locked worker updates also consume the bounded
ledger retry budget; unchanged exhausted generations remain failed after partial
progress. Duplicate comparison stays advisory and never blocks saving work.
After a coherent lexical snapshot is retained, interactive duplicate query
inference and refresh enrollment have a four-second work budget. Source capture
retains the outer 45-second backend response ceiling, with a 50-second MCP ceiling.
A pending native call retains its capacity permit after the interactive answer.


The queued-refresh notice keeps the incomplete comparison visible while creation
remains available: [desktop example](images/search-vectors-pending-desktop.png)
and [narrow example](images/search-vectors-pending-narrow.png).

## Operational changes

Apply migration `0049_duplicate_embeddings` with coordinated 0.76.0 API, MCP,
worker and dashboard code. Existing vector rows are assigned their consumer
purpose from the stored version stamp. Refresh demand is disposable infrastructure
and is excluded from project archives. Downgrade refuses retained `duplicate_embed`
delivery history rather than deleting the journal. Current-head backup and audit
checks require the new schema; historical catalog snapshots remain retained.

`MNEMONIC_DUPLICATE_SUGGESTION_MISSING_VECTOR_LIMIT` is deprecated and ignored;
its existing 1–128 configuration range remains accepted. Background deliveries
process at most 16 stored work items; interactive duplicate checks embed no stored
work items. Caller-supplied external candidates remain a separate bounded lane.

The API logs content-free search phase timings and cache counts at its default
log configuration. Expected `ClientDisconnect` exceptions no longer produce a
server traceback. Typed transcript capacity/busy and search result-size errors
retain their actionable MCP causes instead of reporting an API outage.

Repository validation and merging do not deploy or interrupt production services.
A production migration or service recreation requires separate explicit operator
authorization.
