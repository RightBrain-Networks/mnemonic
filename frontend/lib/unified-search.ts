import { decodeArtifactSearchPage, type ArtifactSearchPage } from "./artifacts.ts";
import { decodeWorkSearchPage } from "./duplicate-handling.ts";
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
    q, facets: ["work_items"], filters: { work_items: filters },
    sort: { by: q && input.semantic ? "relevance" : input.sort === "updated" ? "updated_at" : input.sort === "created" ? "created_at" : "priority", direction: "desc" },
    limit: input.limit, offset: input.offset
  };
}

export function artifactSearchRequest(q: string, fulltext: boolean, includeDeleted: boolean, limit: number, offset: number, workItemId?: string): SearchRequest {
  return { q, facets: ["artifacts"], fulltext, filters: { artifacts: { include_deleted: includeDeleted, ...(workItemId ? { work_item_id: workItemId } : {}) } }, limit, offset };
}

export function transcriptSearchRequest(q: string, fulltext: boolean, offset: number, workItemId?: string): SearchRequest {
  return { q, facets: ["transcripts"], fulltext, filters: { transcripts: { ...(workItemId ? { work_item_id: workItemId } : {}) } }, sort: { by: q ? "relevance" : "created_at", direction: "desc" }, limit: TRANSCRIPT_PAGE_SIZE, offset };
}

function facetPage(value: unknown, facet: SearchFacet, limit: number, offset: number) {
  const page = objectValue(value);
  const totals = objectValue(page?.facet_totals);
  const coverage = objectValue(page?.coverage);
  if (!page || !Array.isArray(page.items) || !finiteInteger(page.total) || page.limit !== limit || page.offset !== offset
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
  return { items, total: page.total, limit, offset, coverage };
}

export function decodeUnifiedWorkSearchPage(value: unknown, projectId: string, options: Parameters<typeof decodeWorkSearchPage>[2] = {}): Page<WorkSearchHit> {
  const page = facetPage(value, "work_items", options.expectedLimit ?? 50, options.expectedOffset ?? 0);
  return decodeWorkSearchPage({ items: page.items, total: page.total, limit: page.limit, offset: page.offset }, projectId, options);
}

export function decodeUnifiedArtifactSearchPage(value: unknown, projectId: string, fulltext: boolean, limit: number, offset: number, includeDeleted = false, workItemId?: string): ArtifactSearchPage {
  const page = facetPage(value, "artifacts", limit, offset);
  const coverage = objectValue(page.coverage.artifacts);
  if (!coverage || typeof coverage.enabled !== "boolean") throw new Error("Mnemonic returned invalid artifact search coverage.");
  const result = decodeArtifactSearchPage({ items: page.items, total: page.total, limit, offset, fulltext, indexing: coverage.indexing, sensitive_content_withheld: coverage.sensitive_content_withheld }, projectId, fulltext, limit, offset);
  if (result.items.some(({ artifact }) => !includeDeleted && artifact.deleted_at !== null
    || workItemId && !sameUuid(artifact.originating_work_item_id, workItemId) && !artifact.related_work_item_ids.some((id) => sameUuid(id, workItemId)))) throw new Error("Mnemonic returned artifacts outside the requested search scope.");
  return result;
}

export function decodeUnifiedTranscriptSearchPage(value: unknown, projectId: string, offset = 0, fulltext = false, workItemId?: string): TranscriptPage {
  const page = facetPage(value, "transcripts", TRANSCRIPT_PAGE_SIZE, offset);
  const coverage = objectValue(page.coverage.transcripts);
  return decodeTranscriptPage({ items: page.items, total: page.total, limit: page.limit, offset, indexing_incomplete: coverage?.indexing_incomplete }, projectId, offset, fulltext, workItemId);
}
