import { validDiagnosticsMode, type DiagnosticsMode } from "./search-exploration.ts";
import { boundedText, exactKeys, finiteInteger, objectValue } from "./wire-guards.ts";

const SOURCES = ["work_items", "artifacts", "transcripts"] as const;
type Source = typeof SOURCES[number];
export type TermDiagnostic = { term: string; matches: Record<Source, number | null> };

export function decodeTermDiagnostics(value: unknown, total: number, searched: Source[], mode: DiagnosticsMode = "on_empty", query?: string): TermDiagnostic[] {
  if (!validDiagnosticsMode(mode) || !Array.isArray(value) || value.length > 500
    || (mode === "off" || mode === "on_empty" && total > 0 || query !== undefined && !query.trim()) && value.length > 0) {
    throw new Error("Mnemonic returned invalid search diagnostics.");
  }
  const seen = new Set<string>();
  return value.map((entry) => {
    const row = objectValue(entry);
    const matches = objectValue(row?.matches);
    if (!row || !exactKeys(row, ["term", "matches"]) || !boundedText(row.term, 1000) || row.term === ""
      || seen.has(row.term) || !matches || !exactKeys(matches, SOURCES)
      || SOURCES.some((source) => searched.includes(source) ? !finiteInteger(matches[source]) : matches[source] !== null)) {
      throw new Error("Mnemonic returned invalid search diagnostics.");
    }
    seen.add(row.term);
    return row as unknown as TermDiagnostic;
  });
}

export const TRANSCRIPT_SEARCH_HINT = 'Agent sessions can be searched by explicitly including "transcripts" in facets or calling search_transcript_contents.';

export function validateSingleFacetScope(value: unknown, facet: Source, searched: Source[]): void {
  const scope = objectValue(value);
  if (!scope || !exactKeys(scope, ["searched_facets", "transcripts", "transcript_search_hint"])
    || !Array.isArray(scope.searched_facets) || scope.searched_facets.length !== searched.length
    || !scope.searched_facets.every((source, index) => source === searched[index])
    || scope.transcripts !== (facet === "transcripts" ? "searched" : "not_selected")
    || scope.transcript_search_hint !== TRANSCRIPT_SEARCH_HINT) {
    throw new Error("Mnemonic returned invalid search scope.");
  }
}
