// Reviewed static explanations; never render upstream text for these rule codes.
export const VALIDATION_RULES: Readonly<Record<string, readonly [string | null, string]>> = {
  "exact_query_requires_text": ["q", "Phrase and literal search require nonblank q."],
  "unclosed_query_phrase": ["q", "Close each quoted phrase, or use query_mode=literal."],
  "query_phrase_requires_terms": ["q", "A phrase needs searchable words; use query_mode=literal for punctuation."],
  "semantic_requires_unconstrained_work_query": ["q", "Semantic work search requires query_mode=terms without quoted phrases."],
  "semantic_requires_all_work_fields": ["work_fields", "Semantic work search requires all work_fields; disable semantic to narrow fields."],
  "content_kinds_requires_fulltext": ["content_kinds", "content_kinds requires fulltext=true."],
  "search_datetime_timezone_required": [null, "Include a timezone offset or Z in each search date bound."],
  "search_created_range_invalid": ["created_before", "created_before must be later than created_after; the upper bound is exclusive."],
  "search_updated_range_invalid": ["updated_before", "updated_before must be later than updated_after; the upper bound is exclusive."],
  "tag_counts_requires_work_facet": ["tag_counts", "tag_counts requires work_items in facets."],

  "view_requires_blank_query": [
    "view",
    "view=roots requires blank q; use view=full for text search."
  ],
  "absolute_http_url_required": [
    null,
    "Include an absolute http:// or https:// URL."
  ],
  "semantic_requires_query": [
    "q",
    "semantic=true requires a nonblank q."
  ],
  "external_url_requires_full_view": [
    "view",
    "external_url requires view=full."
  ],
  "roots_require_canonical_scope": [
    "duplicate_scope",
    "view=roots requires duplicate_scope=canonical."
  ],
  "canonical_filter_requires_alias_scope": [
    "canonical_work_item_id",
    "canonical_work_item_id requires duplicate_scope=aliases or all."
  ]
};
