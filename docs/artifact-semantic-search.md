# Artifact passage search

Artifact semantic search is an explicit body read: use `semantic=true` and
`fulltext=true` on the existing artifact `search-content` safe read, or
`filters.artifacts.semantic=true` with `fulltext=true` and the artifacts facet on
unified search. A nonblank query is required. Semantic retrieval accepts
`query_mode=terms` without quoted phrases; phrase and literal constraints return
a reviewed field rule instead of silently losing exact intent. Date bounds and
`diagnostics=on_empty|always|off` retain their ordinary search meanings. Metadata-only requests do not read
passage vectors or extracted bodies. Query inference shares the existing local
model and inference admission limits with work search and duplicate suggestions.

Every result represents an artifact ranked by its best current passage. The hit's
`evidence: "semantic"` distinguishes passage similarity from a lexical match.
`passage.cosine_similarity` is a value from -1 to 1, labeled
`score_type: "cosine_similarity"` and `score_is_probability: false`; it is not a
confidence estimate. The ordinary hit score is a tied reciprocal passage rank,
labeled `semantic_reciprocal_rank` on the dedicated page. Unified search applies
its existing reciprocal source ranking to these artifact candidates. Each semantic
hit also exposes its one-based native source rank. Semantic
`total_kind: "ranked_candidates"` counts ready artifacts ranked, including weakly
similar artifacts; it is not an exact-match count or a relevance threshold.

Passage evidence includes a stable `passage_id`, artifact revision, extracted
`text_sha256`, Unicode character offsets `[start_offset,end_offset)`, model and
chunk configuration, actual input token count, and input token limit. Open the
existing artifact `/text` read with
`expected_revision`, optional `expected_text_sha256`, `offset=start_offset`, and
`limit=end_offset-start_offset`. A changed extraction returns
`artifact_text_changed` even when the artifact revision is unchanged. Ordinary
revision-only text reads remain valid. Snippets show up to 1,000 characters from
the selected passage; the pinned read recovers the entire passage.

The dedicated page's `embedding` and unified
`coverage.artifacts.embedding` expose generation coverage separately from query
inference availability: ready, pending, processing, failed, unavailable, empty,
withheld artifacts, indexed passage count, and truncated source count. Partial
passage generations do not enter rankings. A zero with pending, failed,
unavailable, truncated, or withheld coverage cannot establish absence. Query
inference failure returns the existing `semantic_unavailable` error instead of
an empty page. More than 100,000 authorized, ready passage vectors returns
`artifact_semantic_capacity`; narrow the source filters. Diagnostics describe
lexical term frequencies in the same authorized content scope, not semantic
scores. Sensitive bodies never contribute to broad agent scores or diagnostics;
targeted dedicated reads keep the existing explicit approval requirements.

Migration `0041_artifact_passages` adds a generated UTF-8 extracted-text SHA-256,
one current generation manifest per artifact, passage vectors, and the
`artifact_embed` background job kind. The shared worker enrolls ready extraction
snapshots automatically. Its job payload contains only a generation UUID and
next character offset; RabbitMQ still carries only a job UUID. Each delivery
reads at most 21,000 text characters, embeds at most 16 overlapping passages of
at most 1,500 characters, and commits a resumable cursor. Passage boundaries use
the exact model tokenizer with counting-time truncation and padding disabled.
Every complete encoding, including special tokens, must fit the native input
window before inference. A passage can therefore be shorter for dense Unicode
text. The next cursor uses its actual end with overlap capped at 200 characters
or one quarter of the passage; even tiny windows advance without gaps. Vector dimension is capped at
4,096 and vectors are validated and normalized before storage. Search streams
64 vectors per database batch and retains only the best locator per artifact.

The effective model name and versioned chunk configuration are part of every
cache witness and passage ID. Configuration v2 binds the full SHA-256 of the
canonical untruncated tokenizer specification and tokenizer runtime version,
plus its actual input limit. Changing the
vocabulary, normalization, token processing, input limit, or chunk algorithm
schedules a fresh generation, and queries exclude generations built with a
different profile. Token counts and limits appear on semantic passage evidence.
Tokenizer preparation happens before scheduling SQL or query database reads;
model failure leaves other job kinds available. No counting operation mutates
the tokenizer shared by normal work and query inference. Canonical extracted
text remains unchanged. Ready extraction truncation
remains explicit coverage. Jobs retry provider failures with bounded backoff.
Disabling the artifact library pauses enrollment and defers existing deliveries
without consuming retry attempts. Exhausted job generations require an operator cache reset; no content or arbitrary
provider errors appear in job error prose.

Publication checks the current artifact revision, extracted-text hash, model,
chunk configuration, generation UUID, cursor and owned job lease after inference.
Replacement/deletion clear obsolete vectors in the same transaction as extracted
text invalidation. Source changes during inference cannot publish old vectors.
Artifact metadata changes keep passage vectors reusable while current sensitivity
and source filters are applied at read time.

Passage tables are rebuildable caches excluded from project archives. Restore
clears affected caches before disabling ordinary foreign-key triggers. Generated
text hashes are recomputed by PostgreSQL on restore. New generation UUIDs prevent
completed background-job receipts from suppressing regenerated work. Canonical
extracted text and revision metadata remain in archives; no raw artifacts are
added to database archives.

Operators may reset one project's passage caches with the existing worker's
configuration and database permissions:

```sh
python -m mnemonic_api.artifact_passage_rebuild --project-id PROJECT_UUID
```

The command removes only that project's derived generations and prints a compact
count. It invalidates in-flight publications, and the ordinary worker scheduler
rebuilds them. No new REST mutation, MCP tool, or operation receipt is introduced.

## Coordinated upgrade

Release 0.60.0 (plugin 0.38.0) requires migration `0041_artifact_passages`. Stop
older API and worker processes, back up PostgreSQL and private file binds, apply
the migration, and upgrade API, MCP, dashboard, plugin, and the shared worker
together. No new environment variable or broker is required. Existing ready
extractions enroll automatically; lexical search remains usable while vectors
backfill. Monitor embedding coverage until the selected corpus is ready. Cache
reset is an operator action and is unnecessary for an ordinary upgrade.

## Dashboard passage retrieval

The artifact library's semantic option requires content search. Results label
ranked candidates and embedding coverage separately from literal matches.
`Open full passage` opens the existing preview with the selected revision,
extracted-text hash, and Unicode offsets. A changed source requests fresh
discovery instead of showing an unpinned substitute. Snippets show at most 1,000
characters; the full pinned passage can contain up to 1,500 characters.

Screenshots: [results desktop](images/artifact-semantic-results-desktop.png),
[results narrow](images/artifact-semantic-results-narrow.png),
[passage desktop](images/artifact-semantic-passage-desktop.png),
[passage narrow](images/artifact-semantic-passage-narrow.png),
[incomplete coverage desktop](images/artifact-semantic-incomplete-desktop.png), and
[incomplete coverage narrow](images/artifact-semantic-incomplete-narrow.png).
The incomplete-coverage screenshots use a disclosed acceptance-test response
variant; passage retrieval cases use the real local embedding model and API.
