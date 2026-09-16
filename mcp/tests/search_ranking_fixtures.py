"""Explicit current wire metadata for older fixtures; native tests opt in before sending."""



def semantic_disposition(status="not_requested", *, scope="none", partial=False, cache="not_needed"):
    return {"inference": {"status": status, "reason": "model_failure" if status == "unavailable" else None},
            "candidate_scope": scope, "partial_vectors": partial,
            "comparison_incomplete": status == "unavailable" or partial or scope == "lexical_shortlist",
            "retry": {"max_attempts": 1, "after_seconds": 1} if status == "unavailable" else None,
            "cache_refresh": {"status": cache, "reason": "cache_refresh_failed" if cache == "failed" else None}}


def add_ranking(page, source, query="", mode="terms", semantic=False):
    def kind(facet):
        return "browsed_records" if not query.strip() else "ranked_candidates" if semantic and facet == "work_items" else "lexical_matches"
    def score(facet):
        return "none" if not query.strip() else "hybrid_reciprocal_rank" if semantic and facet == "work_items" else "postgresql_lexical" if facet == "work_items" else "literal_presence" if mode == "literal" else "tantivy_relevance"
    disposition = semantic_disposition("completed", scope="full_scope") if semantic and query.strip() else semantic_disposition()
    page.setdefault("semantic", disposition)
    if source == "search":
        selected = page.get("search_scope", {}).get("searched_facets", [])
        page.setdefault("facet_total_kinds", {facet: kind(facet) if facet in selected else None for facet in ("work_items", "artifacts", "transcripts")})
        page.setdefault("facet_score_types", {facet: score(facet) if facet in selected else None for facet in ("work_items", "artifacts", "transcripts")})
        kinds = {kind(facet) for facet in selected}
        page.setdefault("total_kind", "mixed" if len(kinds) > 1 else next(iter(kinds), "lexical_matches" if query.strip() else "browsed_records"))
        page.setdefault("score_type", "unified_reciprocal_rank" if query.strip() else "none")
        page["coverage"]["transcripts"].setdefault("unsegmented_content_omitted", 0)
    else:
        page.setdefault("total_kind", kind(source))
        page.setdefault("score_type", score(source))
        if source == "transcripts": page.setdefault("unsegmented_content_omitted", 0)
    counters = {}
    for index, row in enumerate(page["items"], page.get("offset", 0) + 1):
        facet = row["facet"] if source == "search" else source
        item = row[{"work_items": "work_item", "artifacts": "artifact", "transcripts": "transcript"}[facet]] if source == "search" else row
        counters[facet] = counters.get(facet, 0) + 1
        if source == "search":
            row.setdefault("rank", index)
            row.setdefault("score_type", page["score_type"])
        item.setdefault("rank", counters[facet] if source == "search" else index)
        # Transcript detail fixtures explicitly default rank/type to null/none.
        if facet == "transcripts" and item["rank"] is None: item["rank"] = counters[facet] if source == "search" else index
        item.setdefault("score", 0.0)
        item.setdefault("score_type", score(facet))
        if facet == "transcripts" and item["score_type"] == "none" and query.strip(): item["score_type"] = score(facet)
        _evidence_defaults(item, facet, query)
    return page


def _evidence_defaults(item, facet, query):
    if facet == "work_items" and not ("self_matches_filter" in item and "summary" in item):
        item.setdefault("evidence_mode", "lexical" if query.strip() else "browse")
        item.setdefault("matched_fields", ["title"] if query.strip() else [])
        item.setdefault("excerpts", [])
        item.setdefault("excerpts_truncated", bool(query.strip()))
    if facet == "transcripts":
        if not item.get("matched_fields"):
            item["matched_fields"] = ["content"] if item.get("snippet") else ["metadata"] if query.strip() else []
        item.setdefault("snippet_omission_reason", None)
