import { WORK_FIELDS, validWorkFields, validQueryMode, type QueryMode } from "./search-evidence.ts";
import type { SearchRanking } from "./search-ranking.ts";
import { validContentKinds } from "./transcript-segments.ts";
import type { TermDiagnostic } from "./search-diagnostics.ts";
import { SEARCH_DATE_FIELDS, dateBoundsMatch, validDateBounds, validDiagnosticsMode, type DiagnosticsMode } from "./search-exploration.ts";
import { exactKeys, objectValue, sameUuid, validUuid } from "./wire-guards.ts";

export const SEARCH_DISCLOSURE_FIELDS = ["diagnostics", "applied_filters", "query_interpretation", "warnings"] as const;
const sources = ["work_items", "artifacts", "transcripts"] as const;
type Source = typeof sources[number];
export const DEFAULT_SEARCH_FILTERS = {
  work_items: { work_fields: [...WORK_FIELDS], status: "all", tag: null, source_client: null, source_session_id: null, external_url: null, duplicate_scope: "canonical", canonical_work_item_id: null, view: "full" },
  artifacts: { artifact_id: null, work_item_id: null, include_deleted: false, sensitive: null, mime_type: null, created_by_agent_session_id: null },
  transcripts: { content_kinds: null, work_item_id: null, agent_session_id: null, client: null, kind: null, status: null }
};
export type AppliedSearchFilters = { project_id: string; project_ids: null } & Record<Source, Record<string, unknown> | null>;
export type QueryInterpretation = { q: string; query_mode: QueryMode } & Record<Source, { match_mode: string; fields: string[]; fulltext: boolean | null } | null>;
export type SearchDisclosure = {
  diagnostics: DiagnosticsMode;
  applied_filters: AppliedSearchFilters;
  query_interpretation: QueryInterpretation;
  warnings: never[];
};

function validFilter(source: Source, value: unknown): boolean {
  const row = objectValue(value);
  if (!row || !exactKeys(row, [...Object.keys(DEFAULT_SEARCH_FILTERS[source]), ...(source === "artifacts" && "semantic" in row ? ["semantic"] : []), ...SEARCH_DATE_FIELDS.filter((key) => key in row)]) || !validDateBounds(row, true)) return false;
  return Object.entries(row).every(([key, value]) => {
    if (SEARCH_DATE_FIELDS.includes(key as typeof SEARCH_DATE_FIELDS[number])) return value !== null;
    if (key === "semantic") return value === true;
    if (value === null) return !["status", "view", "duplicate_scope", "include_deleted"].includes(key) || source === "transcripts" && key === "status";
    if (key === "work_fields") return validWorkFields(value);
    if (key === "content_kinds") return validContentKinds(value);
    if (["work_item_id", "artifact_id", "canonical_work_item_id"].includes(key)) return validUuid(value);
    if (["include_deleted", "sensitive"].includes(key)) return typeof value === "boolean";
    if (key === "status") return (source === "work_items" ? ["all", "pending", "active", "to-review", "dropped", "deferred", "done", "wont-do", "promoted"] : ["waiting", "pending", "processing", "ready", "failed"]).includes(String(value));
    if (key === "view") return value === "full" || value === "roots";
    if (key === "duplicate_scope") return ["canonical", "aliases", "all"].includes(String(value));
    if (key === "kind") return ["primary", "subagent", "imported"].includes(String(value));
    const maximum = key === "tag" ? 50 : ["client", "source_client"].includes(key) ? 80 : key === "external_url" ? 2000 : key === "mime_type" ? 255 : 200;
    return typeof value === "string" && Array.from(value).length <= maximum;
  });
}

export function decodeSearchDisclosure(value: unknown, projectId: string, searched: Source[]): SearchDisclosure {
  const page = objectValue(value);
  const applied = objectValue(page?.applied_filters);
  const interpreted = objectValue(page?.query_interpretation);
  if (!validDiagnosticsMode(page?.diagnostics) || !applied || !exactKeys(applied, ["project_id", "project_ids", ...sources]) || !sameUuid(applied.project_id, projectId) || applied.project_ids !== null
    || !interpreted || !exactKeys(interpreted, ["q", "query_mode", ...sources]) || !validQueryMode(interpreted.query_mode) || typeof interpreted.q !== "string" || Array.from(interpreted.q).length > 1000 || interpreted.q !== interpreted.q.trim()
    || !Array.isArray(page?.warnings)) throw new Error("Mnemonic returned invalid search disclosure.");
  for (const source of sources) {
    if (!searched.includes(source)) {
      if (applied[source] !== null || interpreted[source] !== null) throw new Error("Mnemonic disclosed an unsearched source as searched.");
      continue;
    }
    const interpretation = objectValue(interpreted[source]);
    if (!validFilter(source, applied[source]) || !interpretation || !exactKeys(interpretation, ["match_mode", "fields", "fulltext"])) throw new Error("Mnemonic returned invalid search interpretation.");
    const isWork = source === "work_items";
    const semanticArtifact = source === "artifacts" && objectValue(applied[source])?.semantic === true;
    const fields = semanticArtifact ? ["content"] : isWork ? objectValue(applied[source])?.work_fields : interpretation.fulltext ? ["metadata", "content"] : ["metadata"];
    const constrained = interpreted.query_mode !== "terms" || interpreted.q.includes('"');
    const modes = semanticArtifact ? ["semantic_passages"] : !interpreted.q ? ["browse"] : interpreted.query_mode === "literal" ? ["literal"] : constrained ? [isWork ? "postgresql_phrase_terms" : "phrase"] : isWork ? ["postgresql_plain_terms_or_substring", "hybrid_lexical_semantic"] : ["all_terms"];
    if (semanticArtifact && (!interpreted.q || constrained || interpretation.fulltext !== true)) throw new Error("Mnemonic returned invalid artifact semantic interpretation.");
    if (!modes.includes(String(interpretation.match_mode)) || (isWork ? interpretation.fulltext !== null : typeof interpretation.fulltext !== "boolean")
      || !Array.isArray(interpretation.fields) || JSON.stringify(interpretation.fields) !== JSON.stringify(fields)) throw new Error("Mnemonic returned invalid search interpretation.");
  }
  if (page.warnings.length !== 0) throw new Error("Mnemonic returned invalid search warnings.");
  return { diagnostics: page.diagnostics as DiagnosticsMode, applied_filters: applied as AppliedSearchFilters, query_interpretation: interpreted as QueryInterpretation, warnings: page.warnings as SearchDisclosure["warnings"] };
}

export function validateSearchDisclosure(disclosure: SearchDisclosure, source: Source, expected: Record<string, unknown>, fulltext?: boolean, query?: string, queryMode: QueryMode = "terms"): void {
  const applied = disclosure.applied_filters[source];
  const interpretation = disclosure.query_interpretation[source];
  if (disclosure.query_interpretation.query_mode !== queryMode || query !== undefined && disclosure.query_interpretation.q !== query.trim()
    || applied && !dateBoundsMatch(applied, expected)
    || applied && Object.entries(expected).some(([key, value]) => ["work_item_id", "artifact_id", "canonical_work_item_id"].includes(key) && value != null ? !sameUuid(applied[key], value) : SEARCH_DATE_FIELDS.includes(key as typeof SEARCH_DATE_FIELDS[number]) ? false : ["content_kinds", "work_fields"].includes(key) ? JSON.stringify(applied[key]) !== JSON.stringify(value) : applied[key] !== value)
    || interpretation && fulltext !== undefined && interpretation.fulltext !== fulltext) throw new Error("Mnemonic returned search disclosure outside the requested scope.");
}

export type FullWorkSearchDetail = SearchRanking & { detail: "full"; work_rank_scope: "work_items"; term_diagnostics: TermDiagnostic[] };
