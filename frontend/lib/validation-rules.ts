// Reviewed static explanations; never render upstream text for these rule codes.
export const VALIDATION_RULES: Readonly<Record<string, readonly [string | null, string]>> = {
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
