# Search exploration controls

This backend slice adds date bounds, optional positive-result diagnostics, and
paginated tag vocabulary to the existing search endpoints. It adds no endpoint,
MCP tool, database migration, background job, or query-time transcript parsing.

## Date bounds

Unified search accepts `created_after`, `created_before`, `updated_after`, and
`updated_before` inside each source filter object. Dedicated work, artifact, and
transcript searches accept the same four fields alongside their existing filters.
Each bound must include a timezone; the server normalizes it to UTC.

Despite the `after` name, lower bounds are inclusive (`>=`); `before` is an
exclusive upper bound (`<`). A pair requires the lower bound strictly before the
upper bound. Omit either end for an open interval. Sources may use different
intervals; an omitted facet is never searched because a filter was supplied.

```json
{
  "facets": ["work_items", "artifacts"],
  "filters": {
    "work_items": {
      "created_after": "2026-09-14T00:00:00Z",
      "created_before": "2026-09-21T00:00:00Z"
    },
    "artifacts": {"updated_after": "2026-09-14T00:00:00Z"}
  }
}
```

Work dates refer to the work item's stored creation/update times. Artifact update
means its stored modification time. Transcript creation means registration time;
update means the last indexing completion, falling back to registration time.
These are the same timestamps used by unified search ordering, not message dates
inside a transcript. Hierarchy `view=roots` preserves ancestor presentation:
a matching descendant can include an older root whose `self_matches_filter` is
false. Ordinary canonical work selection applies date filters to the returned
canonical work item, while a matching alias may supply its lexical evidence.

`applied_filters` echoes active bounds as UTC timestamps. Unset date fields are
omitted; their omission means no bound, not an unknown effective filter. Other
existing filter fields retain their explicit defaults and nulls. Strict response
consumers must distinguish these optional date fields from required disclosure
fields.

## Diagnostics

All four search requests accept `diagnostics` with `on_empty` (default), `always`,
or `off`, and echo it at the response top level. Dedicated work search also
returns the existing `term_diagnostics` shape. `on_empty` computes counts only
when the complete result has zero hits before pagination. `always` also computes
counts on positive results; `off` skips all per-term count work. Blank queries
return an empty list in every mode.

Counts use the same project, source selection, access restrictions, source
filters, date interval, and content inclusion as the search. Unsearched sources
remain null, while searched sources with no matches return zero. Work diagnostics
use the selected `work_fields` and lexical term selection even when the result is
ranked semantically. Phrase/literal diagnostics count individual lexical terms,
not complete exact expressions. Literal searches skip count-index construction
when diagnostics are disabled. Alias/canonical
selection follows the same rules as work search. Counts explain individual term
coverage; they are not causal estimates of how many results removing a term would
add, and they are not semantic relevance scores. Coverage limitations still apply.

```json
{
  "q": "copper zircon",
  "facets": ["work_items"],
  "diagnostics": "always"
}
```

A response may have one hit while reporting two work matches for `copper` and
one for `zircon`. No transcript or artifact count is computed for this request.

## Tag vocabulary

Unified search optionally accepts `tag_counts: {"limit": 50, "offset": 0}`.
Limits are 1–100, offsets 0–1,000,000. `work_items` must be selected. Omitting
`tag_counts` skips vocabulary computation and returns `tag_counts: null`.

```json
{
  "facets": ["work_items"],
  "filters": {"work_items": {"tag": "convergence"}},
  "tag_counts": {"limit": 20, "offset": 0},
  "limit": 5
}
```

The vocabulary comes from matching work identities before result pagination,
including the text query, dates, status, provenance, duplicate scope, and selected
tag. It does not remove the selected tag to calculate hypothetical alternatives.
Each matching member's historical checkpoint tags contribute, using the existing
lowercase exact-tag filter normalization. Values sort by the database's `C`
collation. Repeated checkpoint occurrences do not increase a tag's count.

With default canonical scope, only returned canonical work items' own tags
contribute, matching existing tag-filter behavior; alias-only tags are not
silently inherited. With `duplicate_scope=aliases` or `all`, matching alias tags
also contribute. Every tag's count is distinct canonical identities, even if
several matching aliases or checkpoints carry it. These semantics are disclosed
as `count_unit=canonical_work_items`, `member_scope=returned_work_items`, and
`selected_tag_applied=true`. The member scope describes the matching population,
not only the current result page.

```json
{
  "tag_counts": {
    "items": [{"tag": "convergence", "count": 24}],
    "total": 1,
    "limit": 20,
    "offset": 0,
    "next_offset": null,
    "count_unit": "canonical_work_items",
    "member_scope": "returned_work_items",
    "selected_tag_applied": true
  }
}
```

`total` counts distinct tag values, not work items. `next_offset` is null after
the final page. Result pagination and tag pagination are independent. Repeated
calls are stable for unchanged data; restart pagination after corpus changes.

## Reviewed validation rules

The API returns fixed, value-free rules for missing timezone offsets
(`search_datetime_timezone_required`), unordered created/updated intervals
(`search_created_range_invalid`, `search_updated_range_invalid`), and requesting
tag counts without work selection (`tag_counts_requires_work_facet`). Errors name
the relevant field; nested unified filters preserve their source location.
Manually bounded unified/artifact JSON readers pass reviewed model rules through
the same sanitizer as ordinary FastAPI validation. Invalid JSON and unreviewed
model errors retain their existing generic safe error envelopes.
