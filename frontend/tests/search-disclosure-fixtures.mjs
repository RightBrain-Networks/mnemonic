import { DEFAULT_SEARCH_FILTERS } from "../lib/search-disclosure.ts";

export function disclosure(projectId, selected, { q = "", fulltext = false, filters = {}, semantic = false, diagnostics = "on_empty", query_mode = "terms" } = {}) {
  const sources = ["work_items", "artifacts", "transcripts"];
  const applied_filters = { project_id: projectId };
  const query_interpretation = { q: q.trim(), query_mode };
  const constrained = query_mode !== "terms" || q.includes('"');
  for (const source of sources) {
    const work = source === "work_items";
    applied_filters[source] = selected.includes(source) ? { ...DEFAULT_SEARCH_FILTERS[source], ...filters[source] } : null;
    query_interpretation[source] = selected.includes(source) ? {
      match_mode: !q.trim() ? "browse" : query_mode === "literal" ? "literal" : constrained ? work ? "postgresql_phrase_terms" : "phrase" : work ? semantic ? "hybrid_lexical_semantic" : "postgresql_plain_terms_or_substring" : "all_terms",
      fields: work ? applied_filters[source].work_fields : fulltext ? ["metadata", "content"] : ["metadata"],
      fulltext: work ? null : fulltext
    } : null;
  }
  return { diagnostics, applied_filters, query_interpretation, warnings: [] };
}
