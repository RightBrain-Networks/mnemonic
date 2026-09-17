export function semanticDisposition(mode = "not_requested") {
  const status = mode === "unavailable" ? mode : mode === "not_requested" ? mode : "completed";
  return { inference: { status, reason: status === "unavailable" ? "model_failure" : null }, candidate_scope: status === "completed" ? mode : "none", partial_vectors: false, comparison_incomplete: status === "unavailable" || mode === "lexical_shortlist", retry: status === "unavailable" ? { max_attempts: 1, after_seconds: 1 } : null, cache_refresh: { status: "not_needed", reason: null } };
}
export function ranking(q = "", source = "work_items", semantic = false, query_mode = "terms") {
  return { score_type: !q ? "none" : semantic ? "hybrid_reciprocal_rank" : source === "work_items" ? "postgresql_lexical" : query_mode === "literal" ? "literal_presence" : "tantivy_relevance", total_kind: !q ? "browsed_records" : semantic ? "ranked_candidates" : "lexical_matches", semantic: semanticDisposition(semantic ? "full_scope" : "not_requested") };
}
export function unifiedRanking(selected, options = {}) {
  const { q = "", semantic = false, query_mode = "terms" } = options;
  const native = ranking(q, selected[0], semantic, query_mode);
  return { ...native, total_kind: native.total_kind, score_type: q ? "unified_reciprocal_rank" : "none", facet_score_types: Object.fromEntries(["work_items", "artifacts", "transcripts"].map((source) => [source, selected.includes(source) ? ranking(q, source, source === "work_items" && semantic, query_mode).score_type : null])), facet_total_kinds: Object.fromEntries(["work_items", "artifacts", "transcripts"].map((source) => [source, selected.includes(source) ? ranking(q, source, source === "work_items" && semantic, query_mode).total_kind : null])) };
}
export function hitRanking(q = "", source = "work_items", rank = 1, semantic = false) { return { rank, score: q ? 0.5 : 0, score_type: ranking(q, source, semantic).score_type }; }
export function evidence(memberId, mode = "browse") { return { evidence_mode: mode, matched_fields: mode === "lexical" ? ["title"] : [], excerpts: mode === "lexical" ? [{ field: "title", text: "Supporting text", matched_member_id: memberId, checkpoint_id: null, match_type: "lexical" }] : [], excerpts_truncated: false }; }
