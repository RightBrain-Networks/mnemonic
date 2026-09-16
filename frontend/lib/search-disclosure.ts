import { validContentKinds } from "./transcript-segments.ts";
import { exactKeys, objectValue, sameUuid, validUuid } from "./wire-guards.ts";

export const SEARCH_DISCLOSURE_FIELDS = ["applied_filters", "query_interpretation", "warnings"] as const;
const sources = ["work_items", "artifacts", "transcripts"] as const;
type Source = typeof sources[number];
export const PHRASE_WARNING = "Quoted phrases are not supported; quotation marks do not require adjacent words.";
export const DEFAULT_SEARCH_FILTERS = {
  work_items: { status: "all", tag: null, source_client: null, source_session_id: null, external_url: null, duplicate_scope: "canonical", canonical_work_item_id: null, view: "full" },
  artifacts: { artifact_id: null, work_item_id: null, include_deleted: false, sensitive: null, mime_type: null, created_by_agent_session_id: null },
  transcripts: { content_kinds: null, work_item_id: null, agent_session_id: null, client: null, kind: null, status: null }
};
export type AppliedSearchFilters = { project_id: string } & Record<Source, Record<string, unknown> | null>;
export type QueryInterpretation = { q: string } & Record<Source, { match_mode: string; fields: string[]; fulltext: boolean | null } | null>;
export type SearchDisclosure = {
  applied_filters: AppliedSearchFilters;
  query_interpretation: QueryInterpretation;
  warnings: { code: "phrase_operators_ignored"; sources: Source[]; message: typeof PHRASE_WARNING }[];
};

function validFilter(source: Source, value: unknown): boolean {
  const row = objectValue(value);
  if (!row || !exactKeys(row, Object.keys(DEFAULT_SEARCH_FILTERS[source]))) return false;
  return Object.entries(row).every(([key, value]) => {
    if (value === null) return !["status", "view", "duplicate_scope", "include_deleted"].includes(key) || source === "transcripts" && key === "status";
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
  if (!applied || !exactKeys(applied, ["project_id", ...sources]) || !sameUuid(applied.project_id, projectId)
    || !interpreted || !exactKeys(interpreted, ["q", ...sources]) || typeof interpreted.q !== "string" || Array.from(interpreted.q).length > 1000 || interpreted.q !== interpreted.q.trim()
    || !Array.isArray(page?.warnings)) throw new Error("Mnemonic returned invalid search disclosure.");
  for (const source of sources) {
    if (!searched.includes(source)) {
      if (applied[source] !== null || interpreted[source] !== null) throw new Error("Mnemonic disclosed an unsearched source as searched.");
      continue;
    }
    const interpretation = objectValue(interpreted[source]);
    if (!validFilter(source, applied[source]) || !interpretation || !exactKeys(interpretation, ["match_mode", "fields", "fulltext"])) throw new Error("Mnemonic returned invalid search interpretation.");
    const isWork = source === "work_items";
    const fields = isWork ? ["title", "summary", "tags", "checkpoint", "identifiers", "provenance"] : interpretation.fulltext ? ["metadata", "content"] : ["metadata"];
    const modes = !interpreted.q ? ["browse"] : isWork ? ["postgresql_plain_terms_or_substring", "hybrid_lexical_semantic"] : ["all_terms"];
    if (!modes.includes(String(interpretation.match_mode)) || (isWork ? interpretation.fulltext !== null : typeof interpretation.fulltext !== "boolean")
      || !Array.isArray(interpretation.fields) || JSON.stringify(interpretation.fields) !== JSON.stringify(fields)) throw new Error("Mnemonic returned invalid search interpretation.");
  }
  const expectedWarning = interpreted.q.includes('"') && searched.length ? [{ code: "phrase_operators_ignored", sources: sources.filter((source) => searched.includes(source)), message: PHRASE_WARNING }] : [];
  if (page.warnings.length !== expectedWarning.length || page.warnings.some((entry, index) => {
    const warning = objectValue(entry);
    const expected = expectedWarning[index];
    return !warning || !exactKeys(warning, ["code", "sources", "message"]) || warning.code !== expected.code || warning.message !== expected.message
      || !Array.isArray(warning.sources) || JSON.stringify(warning.sources) !== JSON.stringify(expected.sources);
  })) throw new Error("Mnemonic returned invalid search warnings.");
  return { applied_filters: applied as AppliedSearchFilters, query_interpretation: interpreted as QueryInterpretation, warnings: page.warnings as SearchDisclosure["warnings"] };
}

export function validateSearchDisclosure(disclosure: SearchDisclosure, source: Source, expected: Record<string, unknown>, fulltext?: boolean, query?: string): void {
  const applied = disclosure.applied_filters[source];
  const interpretation = disclosure.query_interpretation[source];
  if (query !== undefined && disclosure.query_interpretation.q !== query.trim()
    || applied && Object.entries(expected).some(([key, value]) => ["work_item_id", "artifact_id", "canonical_work_item_id"].includes(key) && value != null ? !sameUuid(applied[key], value) : key === "content_kinds" ? JSON.stringify(applied[key]) !== JSON.stringify(value) : applied[key] !== value)
    || interpretation && fulltext !== undefined && interpretation.fulltext !== fulltext) throw new Error("Mnemonic returned search disclosure outside the requested scope.");
}

export type FullWorkSearchDetail = { detail: "full"; work_rank_scope: "work_items" };
