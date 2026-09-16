import { type TranscriptContentKind } from "./transcript-segments.ts";
import { decodeSearchDisclosure, validateSearchDisclosure, type SearchDisclosure, type FullWorkSearchDetail } from "./search-disclosure.ts";
import { decodeArtifactSearchPage, type ArtifactSearchPage } from "./artifacts.ts";
import { decodeWorkSearchPage } from "./duplicate-handling.ts";
import { decodeTermDiagnostics, validateSingleFacetScope } from "./search-diagnostics.ts";
import { decodeTranscriptPage, TRANSCRIPT_PAGE_SIZE, type TranscriptPage } from "./transcripts.ts";
import { finiteInteger, objectValue, sameUuid, validUuid } from "./wire-guards.ts";
import type { WorkSearchOptions } from "./work-item-search.ts";
import type { Page, WorkSearchHit } from "./types.ts";

export const SEARCH_FACETS = ["work_items", "artifacts", "transcripts"] as const;
export type SearchFacet = typeof SEARCH_FACETS[number];
type SearchSort = { by: "relevance" | "created_at" | "updated_at" | "priority"; direction: "asc" | "desc" };
export type SearchRequest = {
  q?: string;
  facets?: SearchFacet[];
  fulltext?: boolean;
  detail?: "compact" | "full";
  filters?: Partial<Record<SearchFacet, Record<string, unknown>>>;
  sort?: SearchSort;
  facet_order?: { facet: SearchFacet; sort?: SearchSort }[];
  limit?: number;
  offset?: number;
};

export function unifiedSearchPath(projectId: string): string {
  if (!validUuid(projectId)) throw new Error("Invalid search project.");
  return `/projects/${projectId}/search`;
}

export function workSearchRequest(input: WorkSearchOptions): SearchRequest {
  const duplicateScope = input.duplicateScope ?? "canonical";
  if (input.canonicalWorkItemId && duplicateScope === "canonical") throw new Error("Canonical-group filtering requires aliases or all members.");
  const q = input.query.trim();
  const filters: Record<string, unknown> = { status: input.status, duplicate_scope: duplicateScope };
  for (const [key, value] of Object.entries({ tag: input.tag, source_client: input.sourceClient, source_session_id: input.sourceSessionId, canonical_work_item_id: input.canonicalWorkItemId })) {
    if (value?.trim()) filters[key] = value.trim();
  }
  if (q && input.semantic) filters.semantic = true;
  return {
    q, detail: "full", facets: ["work_items"], filters: { work_items: filters },
    sort: { by: q && input.semantic ? "relevance" : input.sort === "updated" ? "updated_at" : input.sort === "created" ? "created_at" : "priority", direction: "desc" },
    limit: input.limit, offset: input.offset
  };
}

export function artifactSearchRequest(q: string, fulltext: boolean, includeDeleted: boolean, limit: number, offset: number, workItemId?: string): SearchRequest {
  return { q, detail: "full", facets: ["artifacts"], fulltext, filters: { artifacts: { include_deleted: includeDeleted, ...(workItemId ? { work_item_id: workItemId } : {}) } }, limit, offset };
}

export function transcriptSearchRequest(q: string, fulltext: boolean, offset: number, workItemId?: string, contentKinds?: TranscriptContentKind[]): SearchRequest {
  return { q, detail: "full", facets: ["transcripts"], fulltext, filters: { transcripts: { ...(contentKinds ? { content_kinds: contentKinds } : {}), ...(workItemId ? { work_item_id: workItemId } : {}) } }, sort: { by: q ? "relevance" : "created_at", direction: "desc" }, limit: TRANSCRIPT_PAGE_SIZE, offset };
}

function facetPage(value: unknown, projectId: string, facet: SearchFacet, limit: number, offset: number) {
  const page = objectValue(value);
  const totals = objectValue(page?.facet_totals);
  const coverage = objectValue(page?.coverage);
  if (!page || page.detail !== "full" || page.work_rank_scope !== "work_items" || !Array.isArray(page.items) || !finiteInteger(page.total) || page.limit !== limit || page.offset !== offset
    || page.items.length !== Math.min(limit, Math.max(0, page.total - offset))
    || !totals || !SEARCH_FACETS.every((name) => finiteInteger(totals[name]))
    || totals[facet] !== page.total || SEARCH_FACETS.some((name) => name !== facet && totals[name] !== 0)
    || !coverage || typeof page.indexing_incomplete !== "boolean") throw new Error("Mnemonic returned invalid unified search results.");
  const payloadKey = facet === "work_items" ? "work_item" : facet === "artifacts" ? "artifact" : "transcript";
  const ids: string[] = [];
  const items = page.items.map((value) => {
    const hit = objectValue(value);
    const payload = objectValue(hit?.[payloadKey]);
    const identity = facet === "work_items" ? objectValue(objectValue(payload?.summary)?.work_item) : facet === "artifacts" ? objectValue(payload?.artifact) : payload;
    if (!hit || hit.facet !== facet || !validUuid(hit.id) || !payload || !sameUuid(hit.id, identity?.id)
      || typeof hit.score !== "number" || !Number.isFinite(hit.score) || hit.score < 0
      || ![hit.created_at, hit.updated_at].every((time) => typeof time === "string" && Number.isFinite(Date.parse(time)))
      || ["work_item", "artifact", "transcript"].some((name) => name !== payloadKey && hit[name] != null)) throw new Error("Mnemonic returned a search result outside the requested facet.");
    ids.push(hit.id.toLowerCase());
    return payload;
  });
  if (new Set(ids).size !== ids.length) throw new Error("Mnemonic returned duplicate unified search results.");
  const searched = facet === "artifacts" && objectValue(coverage.artifacts)?.enabled === false ? [] : [facet];
  validateSingleFacetScope(page.search_scope, facet, searched);
  const term_diagnostics = decodeTermDiagnostics(page.term_diagnostics, page.total as number, searched);
  return { items, total: page.total, limit, offset, coverage, term_diagnostics, disclosure: decodeSearchDisclosure(page, projectId, searched) };
}

export function decodeUnifiedWorkSearchPage(value: unknown, projectId: string, options: Parameters<typeof decodeWorkSearchPage>[2] = {}): Page<WorkSearchHit> & SearchDisclosure & FullWorkSearchDetail {
  const page = facetPage(value, projectId, "work_items", options.expectedLimit ?? 50, options.expectedOffset ?? 0);
  return decodeWorkSearchPage({ items: page.items, total: page.total, limit: page.limit, offset: page.offset, detail: "full", work_rank_scope: "work_items", ...page.disclosure }, projectId, options);
}

export function decodeUnifiedArtifactSearchPage(value: unknown, projectId: string, fulltext: boolean, limit: number, offset: number, includeDeleted = false, workItemId?: string, query?: string): ArtifactSearchPage {
  const page = facetPage(value, projectId, "artifacts", limit, offset);
  const coverage = objectValue(page.coverage.artifacts);
  if (!coverage || typeof coverage.enabled !== "boolean") throw new Error("Mnemonic returned invalid artifact search coverage.");
  validateSearchDisclosure(page.disclosure, "artifacts", { include_deleted: includeDeleted, work_item_id: workItemId ?? null }, fulltext, query);
  const result = decodeArtifactSearchPage({ items: page.items, total: page.total, limit, offset, detail: "full", fulltext, ...page.disclosure, match_mode: "all_terms", term_diagnostics: page.term_diagnostics, indexing: coverage.indexing, sensitive_content_withheld: coverage.sensitive_content_withheld }, projectId, fulltext, limit, offset, coverage.enabled);
  if (result.items.some(({ artifact }) => !includeDeleted && artifact.deleted_at !== null
    || workItemId && !sameUuid(artifact.originating_work_item_id, workItemId) && !artifact.related_work_item_ids.some((id) => sameUuid(id, workItemId)))) throw new Error("Mnemonic returned artifacts outside the requested search scope.");
  return result;
}

export function decodeUnifiedTranscriptSearchPage(value: unknown, projectId: string, offset = 0, fulltext = false, workItemId?: string, query?: string, contentKinds?: TranscriptContentKind[]): TranscriptPage {
  const page = facetPage(value, projectId, "transcripts", TRANSCRIPT_PAGE_SIZE, offset);
  const coverage = objectValue(page.coverage.transcripts);
  validateSearchDisclosure(page.disclosure, "transcripts", { work_item_id: workItemId ?? null, content_kinds: contentKinds ?? null }, fulltext, query);
  return decodeTranscriptPage({ items: page.items, total: page.total, limit: page.limit, offset, detail: "full", term_diagnostics: page.term_diagnostics, indexing_incomplete: coverage?.indexing_incomplete, ...page.disclosure }, projectId, offset, fulltext, workItemId, contentKinds);
}
