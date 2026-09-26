"""Public search field descriptions shared by schemas and progressive help."""

WORK_SEMANTIC_DESCRIPTION = (
    "Find paraphrases with hybrid lexical/semantic ranking. Requires a nonblank query, "
    "query_mode=terms, no quoted phrases, and all work_fields. Cold or stale vectors can add "
    "inference latency. Check semantic.inference, candidate_scope, partial_vectors, "
    "comparison_incomplete and cache_refresh; total counts ranked candidates, not confident matches."
)
ARTIFACT_SEMANTIC_DESCRIPTION = (
    "Find paraphrases in current artifact passages. Requires fulltext=true, a nonblank query, "
    "query_mode=terms and no quoted phrases. Uses query inference and background passage vectors. "
    "Check embedding coverage and semantic.comparison_incomplete; cosine similarity is not a "
    "probability. Pin returned revision and text_sha256 when reading passage evidence."
)

SEARCH_FIELD_NOTES = {
    "limit": "Requested item ceiling; the 32768-byte page budget can shorten it. "
             "Follow next_offset until null; page_truncated identifies a byte-shortened page.",
    "q": "Query text; query is its alias. Supply at most one spelling. Blank work/unified queries "
         "browse; artifact/transcript content searches require a query. The limit is 1000 characters.",
    "query": "Alias of q; supply at most one spelling. Artifact/transcript content searches require "
             "one spelling; work/unified search can omit both to browse. Limit: 1000 characters.",
    "fulltext": "Include current extracted artifact text or retained transcript text. Metadata-only "
                "is the default. This does not grant access to sensitive artifact content. "
                "Inspect source coverage; broad transcript content searches can be expensive.",
    "query_mode": "terms requires every analyzed term in one record and supports quoted phrases. "
                  "phrase requires adjacent words; literal preserves case, punctuation and spacing. "
                  "Semantic retrieval requires terms without quoted phrases.",
    "work_fields": "Select lexical work evidence fields. Semantic work retrieval requires all six: "
                   "title, summary, tags, checkpoint, identifiers and provenance.",
    "detail": "compact returns bounded discovery pointers; full includes larger metadata/summaries. "
              "Both preserve ranking, totals and coverage. Retrieve exact detail before acting.",
    "diagnostics": "on_empty reports per-term document counts when no records match; always also "
                   "reports them on positive pages, and off skips them. Null means unsearched, "
                   "zero means measured zero. Counts describe lexical coverage, not confidence.",
    "content_kinds": "Requires fulltext=true. Select normalized transcript body kinds; native role "
                     "is independent. Narrowing reduces scanned content while coverage remains explicit.",
    "facets": "Choose work_items, artifacts and/or transcripts. Multi-term defaults omit transcripts; "
              "blank/single-term defaults include all three. Explicit facets controls this selection.",
    "sort": "Order the combined result page. Scores are within-source ordering signals, not "
            "probabilities. Semantic relevance is retained only with relevance sorting.",
    "facet_order": "Place the named facet groups first; other selected sources share the global sort. "
                   "An omitted group sort inherits global sort; offset/limit follow the final order.",
}

SEARCH_TOOLS = frozenset({
    "search", "search_work", "search_artifact_contents", "search_transcript_contents",
})
