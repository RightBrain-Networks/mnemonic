# External records: performance and recovery evidence

Release coordinates: application/API/MCP/dashboard `0.10.0`, Claude plugin
`0.12.0`, migration `0022_external_references`.

The populated backup/restore rehearsal, independent cold reviews, and final
checks are recorded in the
[implementation validation](external-records-implementation-validation.md).

## Performance measurements

Measured September 5, 2026 EDT (September 6 UTC), using synthetic data in
isolated PostgreSQL 17 schemas, Python 3.14, Node 24, and desktop Chromium.
The host was Linux x86-64, Intel Core i7-1360P, with 16 logical CPUs exposed.
Other validation jobs shared the host. These small samples describe observed
behavior; they are neither production latency guarantees nor percentile estimates.
No provider requests, credentials, private issue bodies, or live application
writes were used. Disposable measurement schemas were removed afterward.

### Exact inverse lookup

The fixture contained 3,000 work items in the queried project, each owning one
unique reference. A separate project held the maximum-context fixtures below.
After `ANALYZE`, an ordinary planner-selected
`EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` for
`project_id = :project_id AND deleted_at IS NULL AND external_references @> :refs`
used a bitmap index scan on `ix_work_items_external_references`, followed by a
bitmap heap scan. One row matched, with no recheck or project-filter removals.
Planning took 0.299 ms; execution took 0.179 ms and touched 37 shared buffer
blocks, all hits.

The full REST route, using `view=full`, `status=all`, `duplicate_scope=all`, and
properly encoded `external_url`, took 117–179 ms across five sequential calls
(median 176 ms). It executed 16 SQL statements; database cursor time was
27–44 ms. The route also captures visible work and canonical projections, so
the cheap indexed predicate does not make the whole route constant-time or a
single query. These were warm database-buffer measurements, not a cold-disk test.

### Context size, decoding, and rendering

Each maximum reference list contained ten distinct 2,000-byte URLs, 120 four-byte
Unicode characters per label, full kind/state fields, and observation timestamps.
The context included 100 incoming, 100 outgoing, and 100 undirected counterparts,
plus twenty reference-update events with all five editable fields. Titles and
summaries used accepted control characters that require JSON escaping, exercising
larger wire representations than ordinary prose. PostgreSQL stored each maximum
list as 26,000 bytes of `jsonb::text`; each update's metadata occupied 66,633
bytes, confirming the need for a system-event limit above 64 KiB.

| Actual PostgreSQL/REST fixture | Compact response bytes | Complete REST route | SQL statements | Database cursor time |
| --- | ---: | ---: | ---: | ---: |
| Ordinary one-item context, one short reference | 3,522 | 23–35 ms, three calls | 7 | 8–15 ms |
| 300 counterparts and 20 maximum reference-update events | 9,814,856 | 460–485 ms, three calls | 10 | 154–164 ms |
| Same fanout, plus 22 maximum checkpoint prompts and near-limit checkpoint metadata | 23,383,820 | 624 ms, one call | 10 | 281 ms |

The final row includes the initial checkpoint, a distinct current checkpoint,
and all twenty recent checkpoints. Every prompt contains 100,000 accepted
characters requiring JSON escaping. The maximum-reference fixture took a median
27 ms for Python JSON decoding and 178 ms for backend `WorkContext` validation.
MCP `WorkContext` validation took 283–293 ms for that fixture and 256–312 ms
for the 22-checkpoint fixture, across three samples each.

Browser measurements used the actual response bytes above with a mocked fetch,
so they exclude network and database time. The representative browser fixture
was a separate 11,624-byte context with three counterparts, one short reference
per counterpart, and two short events. Chromium used a 1440×1000 viewport;
each interaction was measured once while other tests shared the host.

| Browser fixture | Node JSON parse / strict decode | Response to context paint | Graph tab | Activity tab | Expand event | Longest main-thread task |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Representative | 0.03 / 3.27 ms | 56.8 ms | 982.9 ms | 75.4 ms | 84.2 ms | No task over 50 ms |
| Maximum references/events | 12.34 / 90.67 ms | 324.4 ms | 1,207.7 ms | 252.6 ms | 116.8 ms | 710 ms |
| Maximum references/events/checkpoint text | 32.28 / 95.64 ms | 544.9 ms | 987.1 ms | 291.8 ms | 116.8 ms | 698 ms |

Graph-tab measurements include lazy module loading, so the representative
Graph timing is not evidence of greater rendering cost. The maximum Graph
views retained all 3,000 counterpart references. No browser error or truncation
occurred; interaction remained possible, with a noticeable roughly 700 ms
main-thread pause at the largest Graph mount. These fixtures are deliberately
extreme and do not establish comfortable reading of thousands of links or
millions of checkpoint characters.

### MCP result-envelope correction

Actual SDK results contain both a text representation and `structuredContent`.
Measuring the complete SDK result exposed a release blocker in the previous
12 MiB stdio result limit. Even the maximum-reference context exceeded that
limit. The coordinated implementation raises only the MCP result envelope to
64 MiB (`67,108,864` bytes). MCP request frames and permanent mutation receipts
retain their separate 1 MiB limits; reference fields, contexts, and events are
not projected away or silently truncated.

| Fixture | HTTP JSON-RPC result bytes | Stdio JSON-RPC frame bytes, including newline |
| --- | ---: | ---: |
| Maximum references/events | 20,327,844 | 20,327,845 |
| Maximum references/events plus all 22 maximum checkpoint prompts | 49,730,241 | 49,730,242 |

Actual HTTP MCP calls completed in 631 ms and 889 ms respectively, including
upstream mock decoding, strict model validation, and SDK serialization. Both
frames fit the revised result limit. The request IDs in those measurements
were small integers; the stronger regression below uses a 128-character ID.

The generated fixture in
[`mcp/tests/external_context_fixture.py`](../mcp/tests/external_context_fixture.py)
also maximizes bounded checkpoint source fields, tags, affected paths, twenty
unresolved gates and twenty resolved gates, including escaped question/resolution
text. It produced a 26,320,456-byte compact context and a 56,036,701-byte SDK
frame with both representations, a 128-character request ID, and the stdio
newline. The actual SDK regression is
[`test_external_context_transport.py`](../mcp/tests/test_external_context_transport.py);
no generated multi-megabyte JSON fixture is committed.

A conservative field-based bound for one complete `WorkContext` SDK frame is
64,317,106 bytes, leaving 2,791,758 bytes below 64 MiB. The calculation includes
both representations and their JSON escaping:

| Section | Conservative combined-frame allowance |
| --- | ---: |
| 22 checkpoints | 31,607,840 bytes |
| 301 reference lists, including the exact work row | 15,968,050 bytes |
| 20 events, including their metadata and provenance | 8,020,000 bytes |
| 300 relationship/counterpart envelopes, excluding references | 4,632,000 bytes |
| 40 gates, including up to 20 resolutions | 3,696,000 bytes |
| Remaining work, canonical identities, counts, revisions, and SDK envelope | 393,216 bytes |

For an accepted string character, the largest combined representation is
13 bytes: six bytes in JSON for a control character and seven in the enclosing
text representation. Arbitrary already-bounded JSON metadata is allowed up to
three times its byte cap. Each checkpoint allowance includes a 1,300,000-byte
combined prompt, 49,152 bytes for metadata, 32,768 for affected paths, 26,000 for
the source URL, 7,800 for other source/branch fields, 13,000 for tags, and 8,000
for keys and identity/time fields. Each reference list receives 53,050 bytes;
URL grammar excludes JSON-escaping characters and a label's largest combined
representation is its 480-byte Unicode spelling twice. Each event receives
401,000 bytes, covering the larger metadata cap and provenance; an ordinary
progress body plus its smaller metadata cap also fits that allowance. Each
adjacent envelope receives 15,440 bytes, including maximum title, relationship
provenance, and a possible public lease. The final allowance includes up to
fifty canonical-path identities, twenty duplicate-member identities, and the
request ID. This bound concerns the current accepted `WorkContext` fields;
expanding those fields requires revisiting the calculation and regression.

### Complete comparison route and contention

The complete authenticated REST suggestion route used the cached
`BAAI/bge-small-en-v1.5` Fastembed model, with 384-dimensional vectors and
`HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`, and the existing local model cache.
The database contained an existing tracker-discovery objective, related research
about task-discovery habits, and unrelated image-thumbnail work. The draft used
the existing objective's title, summary, and initial context, and explicitly
excluded that work ID. Candidate URLs were synthetic `example.com` permalinks.
No embeddings or external records were persisted by the external comparison.

Each row below is one complete request measured under concurrent validation
load. “Cold” means a new lazy model wrapper's first route call; the model files
were already cached locally. Warm requests reused that wrapper.

| Complete route | External count | Elapsed | Internal mode/scope | External scope |
| --- | ---: | ---: | --- | --- |
| Cold, short candidate text | 1 | 1,553 ms | `hybrid_shortlist` / `lexical_shortlist` | `hybrid` |
| Warm, short candidate text | 1 | 455 ms | `hybrid_shortlist` / `lexical_shortlist` | `hybrid` |
| Warm, short candidate text | 16 | 527 ms | `hybrid_shortlist` / `lexical_shortlist` | `hybrid` |
| Warm, short candidate text | 64 | 1,655 ms | `hybrid_shortlist` / `lexical_shortlist` | `hybrid` |
| Warm, no-twin external control | 16 | 495 ms | `hybrid_shortlist` / `lexical_shortlist` | `hybrid` |
| Warm, 1,500-character candidate bodies with competing semantic search | 64 | 5,117 ms | `hybrid_shortlist` / `lexical_shortlist` | `lexical` |

The supplied exact twin ranked first with `exact_title`, `lexical`, and
`semantic` signals in the hybrid calls. Related research ranked second with
lexical and semantic signals. Unrelated database-checkpoint records could also
appear on the semantic lane; in the no-twin control, all ten returned external
results had only the semantic signal. This comparison ranks the caller's finite
population and deliberately has no confidence threshold. A returned item or a
high rank does not establish a duplicate, justify a link, or authorize a merge,
provider write, or closeout.

For contention, the measurement observed entry into a real 64-candidate native
embedding batch, then issued a semantic search concurrently. No artificial
sleep was inserted into the model. The search returned HTTP 503
`semantic_unavailable` after 80 ms while the shared inference permit was owned.
The comparison preserved its internal page and returned the external lexical
baseline after the five-second external budget expired: the exact twin remained
first and research second, without a semantic signal. After actual native
completion, semantic search succeeded with HTTP 200 in 397 ms. These observations
show bounded admission and honest fallback under this load, not native-model
cancellation or guaranteed inference latency. Lexical search and work creation
remain separate from semantic admission; their availability is also covered by
the resource regression suites.

The fixture demonstrates the intended comparison after creation and explicit
self-exclusion. It does not measure private incident recall, provider retrieval
coverage, or quality across a representative production corpus. A worker who
skips lookup and claim remains outside this coordination mechanism.
