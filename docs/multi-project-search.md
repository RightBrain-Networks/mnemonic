# Search across selected projects

MCP `search` accepts exactly one selector: `project_id`, or `project_ids` containing
1–10 unique project UUIDs. REST retains `POST /projects/{project_id}/search` and
adds the safe-read `POST /search` with `project_ids` in its bounded JSON body.
There is no implicit all-projects discovery. A missing or inaccessible project
fails the whole request; it cannot turn into an apparently complete zero result.

Selected projects form one corpus per source. Work candidates share semantic
ranking, and artifacts/transcripts share their source index and relevance
statistics. Facet grouping, sorting, and offset/limit apply once to the combined
results. Equal scores use the existing stable time/facet/UUID tie breakers.

Each hit carries `project_id`, which must agree with its nested item. The
`project_coverage` array identifies every selected project by UUID, name, and
slug, with its facet totals and coverage. Names appear once per project rather
than in every hit. Per-project counts sum to the aggregate counts. Scope echoes
contain either singular `project_id` or the sorted `project_ids`, with the other
selector null. MCP verifies these invariants before returning results. Existing
dashboard views continue to require exactly their selected project.

The cache retains one corpus per source per process, including one combined
project selection; reordering the same selection reuses that cache. Changing the
source corpus replaces it, discarding the old corpus before rebuilding. An unchanged
transcript corpus can also be reused when a newly selected project has no transcripts. Searches do
not run N independently paginated requests or retain an unbounded cache per
project combination. Regression coverage measures cold/warm builds and verifies
replacement after a different selection.

Explicit multi-project requests check combined corpus bounds before hydration:
10,000 work records, 10,000 artifact records, 32 MB metadata per source, and 128 MiB
permitted current artifact body text. Exceeding a bound returns the safe
`multi_project_search_capacity` error with a narrower-selection hint. Transcript
bodies retain the configured `MNEMONIC_TRANSCRIPT_SEARCH_MAX_BYTES` budget across
the entire selection. Metadata-only requests do not load bodies, and sensitive
artifact bodies remain withheld without fresh request-bound approval.

No schema migration, new MCP tool, receipt kind, or write operation is introduced.

An isolated two-project regression fixture measured 85.8 ms for the initial
combined search and 54.4 ms for the warm reordered selection. It verified one
build per source, then replacement when a different source corpus was selected.
These are small-fixture measurements, not production latency targets; query
content, corpus size, and model/cache state affect latency.
