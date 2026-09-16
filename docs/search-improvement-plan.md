Mnemonic search improvement plan

Prepared 2026-09-16 from the supplied agent-consumer report, remote main at `da365aa` (`0.55.0`), targeted live MCP reads, and an isolated in-memory reproduction using the deployed Tantivy library. Implementation authorized by the owner on 2026-09-16. Delivery progress is recorded in search-improvement-status.md.

**Recommendation.** Make search results self-explanatory and affordable before adding broader retrieval. The first delivery should resolve hidden filters, reduce payloads, preserve exact-query intent, and show matching evidence. Persist a shared, structured Mnemonic transcript format during ingestion as the foundation for transcript search and retrieval. Preserve source disclosure, coverage, sensitive-content restrictions, canonical identity, and diagnostic distinctions throughout.

**What the investigation established**

| Finding | Assessment |
| --- | --- |
| P1: hidden pending status | Reproduced live: the reported query returned zero with the omitted status and two with `status="all"`, including deferred and done work. Neither response echoed filters. The default exists in both backend and MCP. |
| P2: ignored phrases | Confirmed in code and with literal quote characters in synthetic queries. `"admission cookie"` and `"cookie admission"` matched both word orders with identical scores. Reversing unquoted terms alone would not have established a phrase defect. |
| P3: excessive payload | Confirmed by contracts and live responses: work hits contain full summaries, context metadata, readiness, and a repeated member pointer. `roots` controls hierarchy rather than payload size. Reported token totals should be treated as workload-specific measurements, not fixed per-item sizes. |
| P4: unexplained work matches | Confirmed structurally: work search also examines checkpoint prose, tags, identifiers, and provenance. The response supplies no field or checkpoint attribution. `fulltext` controls artifact/transcript bodies only. |
| P5: irrelevant transcript snippets | Reproduced live: 115 results for `lease_token_mismatch`, with both sampled snippets showing readiness JSON. Synthetic data exposed two causes: the analyzer splits the identifier into `lease`, `token`, and `mismatch`, and snippet selection can prefer repeated occurrences of one term over the exact identifier elsewhere. Artifacts and transcripts already use the same snippet generator. |
| P6: undiscoverable tags | The current search/tool contracts provide tag filtering but no enumeration or value-count aggregation. |
| P7/P9: semantic totals and scores | Code confirms that semantic work search ranks the eligible corpus without a relevance cutoff and then discards its score details in the specialized response. Unified search uses reciprocal source ranks. These are ordering signals, not calibrated relevance probabilities. |
| P7: inconsistent semantic availability | Work search and duplicate suggestion use the same embedding infrastructure and inference capacity, with different ranking/cache/fallback policies. A successful work search and degraded suggestion do not establish independent semantic services. The cause of the reported degraded call remains unverified. |
| P8: unhelpful validation | Both examples reproduced live. Backend model-level validation becomes `query (value_error)` at the MCP boundary. Two sanitization layers intentionally replace raw validation messages, so the repair must use reviewed messages and field mappings. |
| P10: first-call timeout | Reported, not independently reproduced. Investigate with timing and resource telemetry before choosing a remedy. |

Relevant implementation anchors: [work defaults](../backend/src/mnemonic_api/schemas.py), [MCP work adapter](../mcp/src/mnemonic_mcp/server.py), [work matching](../backend/src/mnemonic_api/services/work_search.py), [query analysis](../backend/src/mnemonic_api/artifact_index.py), [shared snippets](../backend/src/mnemonic_api/artifact_index.py), [rank normalization](../backend/src/mnemonic_api/services/search.py), and [validation formatting](../mcp/src/mnemonic_mcp/validation.py).

**Delivery 1: trustworthy, affordable discovery**

Implement the following as separate, short-lived PRs. Establish the response contract first so subsequent changes do not invent conflicting shapes.

**1. Disclose effective behavior and provide actionable validation. Priority: immediate. Covers P1, interim P2, P8.**

- Echo effective filters on every successful search response, including empty pages. Include defaults such as status, duplicate scope, field scope, and content inclusion. Keep selected/omitted sources in `search_scope`; do not copy a large filter object into each hit.
- Change search discovery defaults to all statuses, including `search_work`. Keep explicit status filters and the separate ready-work queue. Audit dashboard callers and skills for reliance on the old default before release.
- Expose the effective query interpretation per source, including normalized terms when useful. Work PostgreSQL matching and artifact/transcript matching are not identical, so do not label all of them with one inaccurate matching rule.
- Until phrase support ships, a query containing quote operators must produce a structured warning that phrase matching was not applied. Keep `match_mode` a stable enum; put explanations in separate fields rather than inventing values such as `all_terms (phrase operators ignored)`.
- Introduce reviewed validation rule codes with static, actionable messages and MCP-facing locations. Examples: `view_requires_blank_query` on `view` with “view=roots requires blank q; use view=full for text search”; `absolute_http_url_required` on `external_url` with “Include an absolute http:// or https:// URL.”
- Carry these codes through REST, MCP, and dashboard validation. Do not restore arbitrary Pydantic exception text or offending input values. Link the existing handoff-validation issue to this common error work, while keeping its schema-requiredness defect separately testable.

Acceptance: default work discovery finds the same all-status work identities as unified work-only discovery; explicit pending returns the narrower set with visible filters; both reported invalid requests tell the caller how to retry; secret-bearing invalid inputs remain absent from responses and logs.

**2. Make compact output the discovery default. Priority: immediate. Covers P3.**

- Add an orthogonal `detail="compact"|"full"` option to unified and dedicated search tools, defaulting discovery to compact. Retain `view="full"|"roots"` for its existing flat/hierarchy purpose. This avoids making hierarchy and verbosity mutually exclusive.
- For work, include identity, title, lifecycle status, display state, priority, updated time, rank/score type, and bounded matching evidence as it becomes available. Preserve canonical-root identity. Include a different matched member only when it actually differs from the returned root; detail reads retain complete provenance.
- Compact artifacts retain filename, identity, revision, content availability, relevant extraction/coverage flags, and snippet. Compact transcripts retain identity, session/work pointer where available, client, relevant indexing/truncation flags, and snippet. Leave integrity hashes, storage/copy details, complete timestamps, and metadata in existing detail tools unless needed to bind a direct text read.
- Remove upload-limit guidance from artifact search responses. Retain search-relevant enabled/coverage information.
- Start with a default limit of 20 and bounded title/snippet lengths. Proposed acceptance target: at least 75% fewer serialized bytes than full output on representative work pages, and a 20-hit page below roughly 5,000 tokens with a named benchmark tokenizer. Measure actual wire output; these are targets, not current measurements.
- Hydrate only the returned page and avoid constructing full summaries merely to throw most fields away. The specialized semantic path currently prepares summaries for the whole scoped population; address that cost alongside response compaction.
- Have dashboard consumers request the detail they actually need. Update strict response models, adapters, and decoders together; adding a field in the API alone is insufficient.

Acceptance: compact/full modes return identical identities, order, totals, and coverage for the same search; long checkpoint histories do not inflate compact payloads; a canonical alias match remains traceable; existing detail reads still provide full context.

**3. Normalize every transcript into a shared Mnemonic format during ingestion. Priority: foundational; begin alongside items 1 and 2. Added from the owner's follow-up.**

Introduce a persisted, versioned, Mnemonic-native transcript representation shared by Claude Code, Codex, and future clients. Current parsers already flatten native transcripts into common text; this work preserves a common structured conversation before text extraction. Client-specific adapters run at ingestion, so search, snippets, and ordinary retrieval consume the same representation regardless of the producing client.

Proposed pipeline:

```text
Verified native capture -> Retained original -> Client adapter -> Mnemonic transcript
                                                                   |             |
                                                              Search index   Retrieval
```

- Apply the pipeline to every ingestion path: workspace folder imports, primary/subagent enrollment, and historical backfill. “Upon import” means a durable background stage after a verified capture and before the new representation is published for indexing/retrieval; do not perform normalization in a query or lengthen the import request into a synchronous conversion. Preserve existing active-lease and paused-project guards.
- Define a schema version and normalizer version independently. Prefer a streamable representation such as a manifest plus typed JSONL records. Give each normalized representation an immutable revision identity bound to its captured source identity/checksum and schema/normalizer versions. Segment IDs are deterministic within that revision and independent of transient job or index-rebuild generations. Keep source bytes/hash, normalized bytes/hash, and derived search-text hash distinct.
- Model ordered events and content blocks explicitly: stable event/block IDs, native record locators, roles, content kinds, timestamps when present, text, tool name, native call/result correlation, and parent/branch relationships when present. Keep tool arguments/results structured where the native format supplies structure. Missing timestamps, unsupported blocks, truncation, and broken references receive explicit dispositions; do not invent missing facts. Project, work, and lease associations remain under the existing database authority.
- Distinguish role from content kind. A user-role message can contain a tool result; filtering authored user text must not include it. Preserve source relationships and avoid duplicating native records that are known alternate representations of the same event. Keep normalization deterministic and structural; generated summaries are a separate possible feature.
- Retain the exact native copy as immutable provenance and the input for future parser corrections. Store the Mnemonic format as the common working representation. Existing public transcript downloads and text reads already return normalized UTF-8 text pinned by `text_sha256`, while `sha256` identifies native bytes. Preserve those meanings and add structured conversation/segment retrieval bound to the new normalized revision. Correct the stale `get_transcript_text` description in `mcp/src/mnemonic_mcp/transcript_tools.py` that still says original bytes are not retained.
- Derive searchable text and segment locators from the normalized records through one shared extraction path. Preserve segment boundaries through any Tika processing and text cleanup. Bind snippets, bounded retrieval, and pagination to an immutable normalized revision and segment ID; never apply offsets from one representation or revision to another. Retrieval should return a selected conversation range without requiring an agent to download or interpret the native client file.
- Publish completed normalized revisions atomically using the existing durable job infrastructure, ownership checks, lock order, and stale-generation checks. An imported transcript can retain its public ID when enrollment creates a new capture; reject publications against the former snapshot or execution generation. Version the derived index against the normalized revision. Normalization status/errors and unsupported/truncated coverage must be observable separately from successful raw copying and successful indexing.
- Plan a schema migration and resumable backfill from retained copies. Preserve prior usable search text when normalization fails, but report incomplete structured coverage and do not pretend legacy text has message boundaries. Re-indexing can rebuild from the normalized representation; changing the adapter/schema regenerates it from the retained original. Include retained originals and normalized storage in backup/restore verification and storage accounting. Avoid applying snippet or search-extraction limits as an implicit truncation policy for the persisted transcript itself.

Implementation anchors: `transcript_parsers.py` and its factory, `transcript_indexing.py`, transcript copy/snapshot storage, the durable job ledger/worker, transcript schemas, and text/download consumers. Reuse these components; no new MCP write tool is needed.

Acceptance: equivalent Claude and Codex conversations yield the same logical event/block shape and the same role/content-kind search behavior; call/result relationships and event order survive; repeated normalization of the same captured input with the same versions produces identical output and IDs; ordinary index rebuilds preserve normalized revision/segment identity, while a new source capture or normalizer version cannot publish stale excerpts; indexing/retrieval need no native parser after successful normalization; query-time retrieval can open a hit with surrounding events directly; interrupted/backfilled normalization resumes safely, preserves original checksums, and reports incomplete coverage.

Dependencies: agree on this schema before implementing structural transcript snippets and content-kind filtering (items 4 and 8). Items 1 and 2, work/artifact matching improvements, and the existing-text snippet repair can ship independently. Implement transcript message-boundary guarantees against this shared representation rather than building a second segmentation format.

**4. Preserve phrase/identifier intent and fix snippet selection together. Priority: immediate. Covers P2 and P5.**

- Define a small explicit query contract: all-terms search remains the default; quoted spans require ordered adjacent terms; an explicit literal mode finds a contiguous identifier or error string. Document case, accent, whitespace, and punctuation behavior. An analyzed phrase is not automatically a byte-exact string match.
- Implement those semantics consistently across PostgreSQL work matching and Tantivy artifact/transcript matching. Phrase occurrences must not be synthesized across unrelated fields or checkpoints. Transcript message-boundary guarantees use the normalized event/block structure from item 3; legacy text without established boundaries must report that limitation.
- Reject unsupported combinations with a useful error instead of silently dropping the requested semantics. During staged delivery, clearly report any unsupported source.
- Make snippets prefer a qualifying phrase/literal span, then coverage of distinct query terms and proximity for all-terms queries. Repeated occurrences of one common term must not beat a nearby complete identifier. Use a small bounded set of excerpts when terms are genuinely far apart; identify which terms each excerpt supports.
- Return field/source attribution. A metadata-only match must not receive an unrelated content snippet. Preserve plain-text output, snapshot consistency, and page-only body hydration.
- Version and rebuild derived indexes if analyzers or schema change. Do not modify retained artifact or transcript source bytes.

Acceptance: reversed quoted phrases produce different results on the synthetic fixture; literal `lease_token_mismatch` excludes a document containing only scattered component words; when the identifier occurs late after repetitive readiness JSON, its snippet shows that occurrence; ordinary all-terms recall and existing coverage behavior remain intact.

**5. Explain and scope work matches. Priority: next within Delivery 1. Covers P4.**

- Add `matched_fields` and bounded excerpts to both work search front doors. Include the matched checkpoint ID and matched member ID when checkpoint or alias text supplies evidence.
- Support explicit `work_fields`, at least title, summary, tags, checkpoint prose, identifiers, and provenance. The last two matter because they are searchable today. Keep existing broad recall as the default, but echo it.
- Derive attribution and excerpts from the same matching decision and database snapshot that selected the result. A second substring search is insufficient because stemming, stop words, fallback substring matching, and canonical aliases can differ.
- Preserve the current unit of matching: do not inadvertently combine unrelated checkpoint fragments into a new apparent match. Add field-specific indexes only if query measurements justify them; index migrations/backfills belong with this PR if introduced.
- For semantic-only results, label semantic evidence separately; do not invent lexical `matched_fields` or claim the displayed excerpt contains a literal match.

Acceptance: a checkpoint-only hit explains itself without `recall_work`; excluding checkpoint/provenance fields removes it; stemmed matches name the correct field and excerpt; alias evidence points to the actual member while the result keeps the canonical identity.

**Delivery 2: better exploration and honest relevance**

**6. Add tag discovery, date filters, and optional diagnostics on positive results. Covers P6 and missing functionality 3/8.**

- Add optional, paginated tag counts to the existing search response. A blank query can enumerate the project's tag vocabulary. Avoid a new MCP tool solely for enumeration; preserve the 55-tool catalog.
- Count distinct work identities under explicitly documented canonical/alias and filter semantics, normalize tags exactly as the tag filter does, and disclose pagination/truncation. Decide and state whether the active tag filter is included in aggregation scope.
- Add created/updated date ranges with UTC timestamps and explicit inclusive-lower/exclusive-upper bounds. Preserve each source's existing timestamp meaning; transcript `updated_at` means indexing disposition, not conversation time.
- Add `diagnostics="on_empty"|"always"|"off"`, retaining `on_empty` as the inexpensive default. `always` returns existing per-term counts even with positive totals. Counts use the same filters and access restrictions, including field and date scopes.
- Individual-term counts cannot establish how much removing a term would increase the conjunction. Do not describe them as causal recall-loss estimates; leave-one-term-out counts would be a separate measured extension.
- Update validation that currently rejects diagnostics when `total > 0`, including MCP and frontend guards. Preserve null for unsearched sources and measured zero for searched sources.

Acceptance: an agent discovers an unfamiliar tag from one search response; tag counts avoid checkpoint/alias double counting; date boundaries work across time zones; a one-hit query can explain its terms without loosening filters or searching omitted transcripts.

**7. Expose ranking meaning and diagnose semantic degradation/timeouts. Covers P7, P9, P10.**

- Return rank and score type consistently. Suggested types distinguish PostgreSQL lexical relevance, Tantivy relevance, hybrid reciprocal-rank fusion, and unified reciprocal source rank. If raw component scores are exposed, label them and keep them out of compact output unless useful.
- Label totals per facet as lexical matches or ranked candidates. A mixed page containing semantic work candidates must not present its entire total as an exact textual match count.
- Keep current ranking available while building an evaluation set. Rescaling scores to 0–1 does not create a confidence scale; do not introduce a universal cutoff until relevance judgments support it.
- Share availability/reason vocabulary across work search and duplicate suggestion without forcing them to use identical candidate composition. Report such distinctions as inference capacity unavailable, model failure, lexical shortlist coverage, and partial vector availability using safe codes.
- Instrument queue wait, query embedding, cache refresh, candidate selection, inference, and total duration. Compare cold/warm caches, cache contention, and exhausted inference capacity. The current suggestion path has one inference slot, a short admission wait, and a 60-second deadline; diagnose against those bounds before changing them.
- Keep duplicate checking advisory. Timeouts/fallbacks communicate an incomplete comparison and a bounded retry option, never “no duplicates.” Do not turn availability into a prerequisite for saving work.
- Evaluate whether cross-source candidate reranking improves judged relevance after snippets and evidence are available. Reciprocal rank currently treats the first hit from each source equally; better cross-source relevance requires additional evidence, not cosmetic score normalization.

Acceptance: a semantic result states whether total is corpus size; controlled inference saturation yields a clear, consistent disposition; cold-start latency and failure causes are measurable; any new threshold/reranker improves judged retrieval without materially harming recall.

**Delivery 3: expanded retrieval, after the first two deliveries are measured**

**8. Search transcript segments by content kind. Covers missing functionality 9.**

Build on the shared Mnemonic transcript format from item 3. Offer filters for human text, assistant text, tool calls, and tool results using its established role/content-kind fields. Return the normalized revision and segment locator with matching evidence, and use the same representation for bounded surrounding-context retrieval. Reuse its backfill and coverage reporting; do not introduce another native parser or a competing segment schema in search. Role alone remains insufficient because tool results can occur inside user-role messages.

Acceptance: excluding tool results removes a quoted Mnemonic error while retaining an authored account of encountering it; a synthetic user-role tool-result block does not pass a human-text filter; both Claude and Codex formats behave consistently.

**9. Add chunk-level semantic artifact retrieval. Covers missing functionality 7.**

Split extracted documents into bounded, locatable passages and embed those passages using existing local embedding infrastructure. A single embedding of a 630 KB document is insufficient. Key vectors by artifact revision, text hash, model, and chunking configuration; rank passages and group results by artifact. Keep content search opt-in, preserve sensitive-content access rules across candidate selection/scores/diagnostics, invalidate replaced/deleted content, and expose embedding coverage. Plan migration, bounded background work, and rebuild behavior explicitly.

Acceptance: a paraphrased question retrieves a relevant passage near the end of a long document; metadata-only searches do not consult body embeddings; replacement/deletion and withheld sensitive content cannot contribute stale or unauthorized results.

**10. Add explicit multi-project discovery. Covers missing functionality 2.**

Start with a bounded explicit `project_ids` selection on unified search, mutually exclusive with singular `project_id`. An all-projects mode must be explicit and limited to accessible projects. Return project identity per hit, per-project coverage, and one globally ranked/paginated result set. Do not concatenate independently paginated project pages. Current artifact/transcript index caches retain one corpus per process, so measure cache replacement and establish bounded reuse before enabling broad fan-out.

Acceptance: a query across two projects finds the same eligible records as the union of their searches, with deterministic global pagination and clear project provenance; omitted/inaccessible projects and incomplete source coverage are explicit; memory and cold-build work remain bounded.

**Evaluation and release gates**

Build a reviewed fixture set from the report's failure shapes, with synthetic or deliberately selected content: all-status history, reversed phrases, identifiers with underscores, long repetitive transcripts, checkpoint-only and stemmed matches, aliases, positive-result diagnostics, semantic paraphrases, sensitive artifacts, and incomplete indexing. Add paired Claude/Codex normalization fixtures covering equivalent conversations, native mirrored records (including retained Codex Plan/FunctionCallOutput exceptions), branches, tool calls/results, unknown blocks, absent timestamps, deterministic IDs, imported-to-enrolled capture changes, index rebuilds, and interrupted backfill. Retain a small judged query set for ranking changes.

Measure retrieval quality at a fixed result count and fixed context budget; useful-hit precision/recall; snippet support for the query; serialized bytes and tokenizer-specific tokens; detail-read round trips; and cold/warm latency. Set numerical ranking/latency targets after capturing the baseline. Payload reduction has the explicit proposed target in item 2.

Extend existing PostgreSQL search, canonical-group, artifact lifecycle, and transcript search tests; run the real Tantivy fixture tests; update MCP contract/response validation and frontend decoder tests. PostgreSQL-marked tests must actually run. Dashboard changes require the relevant browser acceptance checks. Required CI must pass through the normal topic-branch PR workflow.

Keep backend, MCP, dashboard, plugin guidance, portable skills, and validation vocabulary synchronized. Correct the compact-pointer promise only when the payload fulfills it; until then document the actual shape and encourage small limits. Fix the FishFood doctrine separately in its own repository. Keep three plugin skills and avoid expanding the tool catalog solely for tag enumeration. User-facing deliveries take MINOR application releases; 0.56.0 is the next candidate if 0.55.0 remains current. The shared transcript format and vector schema changes receive explicit migrations, resumable backfill/reindex plans, and coordinated consumer updates. README remains human-authored and read-only.

The pending remediation-summary lead `bc0128a5-8bda-4f82-9047-b2423a43ffd2` should be reconciled against the implementation already shipped in 0.47.0 and its [search regression](../backend/tests/test_code_reviews_postgres.py). Do not schedule the same summary-generation fix again or rewrite historical checkpoints. The pending validation lead `367aa78e-d366-4536-b233-b77f811f638e` is relevant to item 1, but its schema/validator mismatch remains a separate acceptance case. No backlog item was changed during this review.
