import { DEFAULT_SEARCH_FILTERS } from "../lib/search-disclosure.ts";

export function disclosure(projectId, selected, { q = "", fulltext = false, filters = {}, semantic = false, diagnostics = "on_empty", query_mode = "terms" } = {}) {
  const sources = ["work_items", "artifacts", "transcripts"];
  const applied_filters = { project_id: projectId, project_ids: null };
  const query_interpretation = { q: q.trim(), query_mode };
  const constrained = query_mode !== "terms" || q.includes('"');
  for (const source of sources) {
    const work = source === "work_items";
    const semanticArtifact = source === "artifacts" && filters.artifacts?.semantic === true;
    applied_filters[source] = selected.includes(source) ? { ...DEFAULT_SEARCH_FILTERS[source], ...filters[source] } : null;
    query_interpretation[source] = selected.includes(source) ? {
      match_mode: semanticArtifact ? "semantic_passages" : !q.trim() ? "browse" : query_mode === "literal" ? "literal" : constrained ? work ? "postgresql_phrase_terms" : "phrase" : work ? semantic ? "hybrid_lexical_semantic" : "postgresql_plain_terms_or_substring" : "all_terms",
      fields: semanticArtifact ? ["content"] : work ? applied_filters[source].work_fields : fulltext ? ["metadata", "content"] : ["metadata"],
      fulltext: work ? null : fulltext
    } : null;
  }
  return { diagnostics, applied_filters, query_interpretation, warnings: [] };
}

export function projectCoverage(page, projectId) {
  page.project_coverage = [{ project_id: projectId, project_name: "Search project", project_slug: "search-project", facet_totals: page.facet_totals, coverage: page.coverage, indexing_incomplete: page.indexing_incomplete }];
  return page;
}
