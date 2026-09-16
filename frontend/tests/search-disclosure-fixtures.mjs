import { DEFAULT_SEARCH_FILTERS, PHRASE_WARNING } from "../lib/search-disclosure.ts";

export function disclosure(projectId, selected, { q = "", fulltext = false, filters = {}, semantic = false } = {}) {
  const sources = ["work_items", "artifacts", "transcripts"];
  const applied_filters = { project_id: projectId };
  const query_interpretation = { q: q.trim() };
  for (const source of sources) {
    const work = source === "work_items";
    applied_filters[source] = selected.includes(source) ? { ...DEFAULT_SEARCH_FILTERS[source], ...filters[source] } : null;
    query_interpretation[source] = selected.includes(source) ? {
      match_mode: !q.trim() ? "browse" : work ? semantic ? "hybrid_lexical_semantic" : "postgresql_plain_terms_or_substring" : "all_terms",
      fields: work ? ["title", "summary", "tags", "checkpoint", "identifiers", "provenance"] : fulltext ? ["metadata", "content"] : ["metadata"],
      fulltext: work ? null : fulltext
    } : null;
  }
  return { applied_filters, query_interpretation, warnings: q.includes('"') && selected.length ? [{ code: "phrase_operators_ignored", sources: sources.filter((source) => selected.includes(source)), message: PHRASE_WARNING }] : [] };
}
