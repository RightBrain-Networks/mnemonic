import { infiniteQueryOptions, queryOptions, type InfiniteData, type QueryClient, type QueryKey } from "@tanstack/react-query";
import { api, workItemPath } from "./api.ts";
import { decodeHierarchySearchPage } from "./hierarchy-presentation.ts";
import { nextPageOffset } from "./page-offsets.ts";
import { queryKeys } from "./query-keys.ts";
import type { DuplicateScope, Page, StatusFilter, WorkSort } from "./types.ts";
import { decodeUnifiedWorkSearchPage, unifiedSearchPath, workSearchRequest } from "./unified-search.ts";
import { isFlatWorkSearch, workSearchParams } from "./work-item-search.ts";
import { WORK_PAGE_SIZE, type WorkQueueItem } from "./work-queue.ts";

export type WorkQueueFilters = {
  projectId: string;
  status: StatusFilter;
  sort: WorkSort;
  search: string;
  semantic: boolean;
  duplicateScope: DuplicateScope;
  canonicalWorkItemId: string;
  tag: string;
  sourceClient: string;
  sourceSessionId: string;
  pinnedWorkItemIds: readonly string[];
};

export type WorkQueueChunk = {
  pages: Page<WorkQueueItem>[];
  loadedThrough: number;
  nextOffset: number | null;
  appended: boolean;
};
type QueuePageParam = { offset: number; index: number };
export type WorkQueueQueryData = InfiniteData<WorkQueueChunk, QueuePageParam>;
type QueuePageReader = (filters: WorkQueueFilters, offset: number, signal: AbortSignal) => Promise<Page<WorkQueueItem>>;

export async function fetchWorkQueuePage(filters: WorkQueueFilters, offset: number, signal: AbortSignal): Promise<Page<WorkQueueItem>> {
  const { projectId, status, sort, search, semantic, duplicateScope, canonicalWorkItemId, tag, sourceClient, sourceSessionId, pinnedWorkItemIds } = filters;
  const options = {
    status, statusScope: "work_item" as const, sort, limit: WORK_PAGE_SIZE, offset,
    query: search, semantic, duplicateScope, tag, sourceClient, sourceSessionId,
    pinnedWorkItemIds, ...(canonicalWorkItemId ? { canonicalWorkItemId } : {})
  };
  const expectedFilters = { status_scope: "work_item" as const, status, tag: tag.trim() || null, source_client: sourceClient.trim() || null, source_session_id: sourceSessionId.trim() || null };
  if (isFlatWorkSearch({ query: search, duplicateScope, canonicalWorkItemId })) {
    const value = await api<unknown>(unifiedSearchPath(projectId), {
      method: "POST", body: JSON.stringify(workSearchRequest(options)), signal
    });
    return decodeUnifiedWorkSearchPage(value, projectId, {
      duplicateScope, canonicalWorkItemId: canonicalWorkItemId || undefined,
      query: search, semantic: Boolean(search.trim() && semantic), expectedFilters,
      expectedLimit: WORK_PAGE_SIZE, expectedOffset: offset
    });
  }
  const value = await api<unknown>(`${workItemPath(projectId)}?${workSearchParams(options)}`, { signal });
  return decodeHierarchySearchPage(value, projectId, WORK_PAGE_SIZE, offset, expectedFilters);
}

export function workQueueQueryOptions(client: QueryClient, filters: WorkQueueFilters, readPage: QueuePageReader = fetchWorkQueuePage) {
  const queryKey: QueryKey = queryKeys.workQueue(filters.projectId, filters);
  return infiniteQueryOptions({
    queryKey,
    initialPageParam: { offset: 0, index: 0 } as QueuePageParam,
    queryFn: async ({ pageParam, signal }): Promise<WorkQueueChunk> => {
      const cached = client.getQueryData<WorkQueueQueryData>(queryKey);
      const previousEnd = cached?.pages[pageParam.index]?.loadedThrough ?? pageParam.offset + 1;
      // Larger refreshed wire pages can already cover a later cached group.
      // Retain its continuation without fetching beyond the previous prefix.
      if (pageParam.index > 0 && cached?.pages[pageParam.index] && previousEnd <= pageParam.offset) {
        return { pages: [], loadedThrough: pageParam.offset, nextOffset: pageParam.offset, appended: false };
      }
      const pages: Page<WorkQueueItem>[] = [];
      let offset = pageParam.offset;
      let nextOffset: number | null;
      // Keep the previously loaded prefix even if the response byte budget now
      // produces shorter pages. New appends still request one wire page.
      do {
        const page = await readPage(filters, offset, signal);
        pages.push(page);
        nextOffset = nextPageOffset(page);
        if (!page.items.length || nextOffset === null) break;
        offset = nextOffset;
      } while (offset < previousEnd);
      const last = pages[pages.length - 1];
      return { pages, loadedThrough: last.offset + last.items.length, nextOffset,
        appended: pageParam.index > 0 && !cached?.pages[pageParam.index] };
    },
    getNextPageParam: (last, pages): QueuePageParam | undefined => last.nextOffset === null
      ? undefined : { offset: last.nextOffset, index: pages.length },
    retry: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false
  });
}

export type CanonicalWorkFilters = { projectId: string; excludedWorkId: string; query: string; limit: number };

export function canonicalWorkQueryOptions({ projectId, excludedWorkId, query, limit }: CanonicalWorkFilters) {
  const trimmedQuery = query.trim();
  const queryKey: QueryKey = queryKeys.canonicalWork(projectId, { excludedWorkId, query: trimmedQuery, limit });
  return queryOptions({
    queryKey,
    queryFn: async ({ signal }) => {
      const body = workSearchRequest({ status: "all", sort: "updated", limit, offset: 0, query: trimmedQuery, duplicateScope: "canonical" });
      const value = await api<unknown>(unifiedSearchPath(projectId), { method: "POST", body: JSON.stringify(body), signal });
      const page = decodeUnifiedWorkSearchPage(value, projectId, {
        duplicateScope: "canonical", query: trimmedQuery, expectedFilters: { status: "all" }, expectedLimit: limit, expectedOffset: 0
      });
      return { ...page, items: page.items.filter((item) => item.summary.work_item.id.toLowerCase() !== excludedWorkId.toLowerCase()) };
    },
    retry: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false
  });
}
