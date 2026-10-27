"use client";

import { useEffect } from "react";
import { useInfiniteQuery } from "@tanstack/react-query";
import { api, errorMessage } from "@/lib/api";
import { decodeTaskPage, TASK_PAGE_SIZE } from "@/lib/tasks";
import { earliestLeaseExpiry, scheduleLeaseExpiryRefresh } from "@/lib/lease-refresh";
import { useFailedReadRetry } from "@/components/use-failed-read-retry";
import { queryKeys } from "@/lib/query-keys";
import type { WorkQueueFilters } from "@/lib/work-queries";
import type { ReviewQueueItem } from "@/lib/work-queue";

export function useReviewQueuePages(filters: WorkQueueFilters, enabled: boolean) {
  const status = filters.status === "to-review" ? "pending" : filters.status;
  const query = useInfiniteQuery({
    queryKey: [...queryKeys.tasks(filters.projectId), "work-queue", filters, enabled],
    enabled, initialPageParam: 0,
    queryFn: async ({ signal, pageParam }) => {
      const params = new URLSearchParams({ kind: "code_review", status,
        offset: String(pageParam), limit: String(TASK_PAGE_SIZE), sort: filters.sort, include_summary: "true" });
      if (filters.search.trim()) params.set("q", filters.search.trim());
      for (const tag of filters.tags ?? []) params.append("tags", tag);
      params.set("tag_mode", filters.tagMode ?? "or");
      const result = await api<unknown>(`/projects/${filters.projectId}/tasks?${params}`, { signal });
      const page = decodeTaskPage(result, filters.projectId, status, "code_review", pageParam);
      if (page.items.some((task) => !task.work_summary)) throw new Error("Mnemonic omitted a review’s work summary.");
      return page;
    },
    getNextPageParam: (last) => last.offset + last.items.length < last.total ? last.offset + last.items.length : undefined,
    retry: false
  });
  const pages = enabled ? query.data?.pages : undefined;
  const expiry = earliestLeaseExpiry(pages?.map((page) => page.next_lease_expires_at) ?? []);
  useEffect(() => enabled && expiry ? scheduleLeaseExpiryRefresh(expiry, () => void query.refetch()) : undefined, [enabled, expiry, query.refetch]);
  const error = enabled && query.isError && !query.isFetchNextPageError ? errorMessage(query.error) : "";
  const appendError = enabled && query.isFetchNextPageError ? errorMessage(query.error) : "";
  const retry = () => { if (enabled) void query.refetch(); };
  const loadMore = () => { if (enabled && query.hasNextPage && !query.isFetching) void query.fetchNextPage({ cancelRefetch: false }); };
  const scope = JSON.stringify(filters);
  useFailedReadRetry({ scope: `review-queue:${scope}`, failed: Boolean(error), busy: query.isFetching, enabled, retry });
  useFailedReadRetry({ scope: `review-queue-append:${scope}`, failed: Boolean(appendError), busy: query.isFetching, enabled, retry: loadMore });
  const seen = new Set<string>();
  const items: ReviewQueueItem[] = (pages ?? []).flatMap((page) => page.items).flatMap((task) => {
    if (seen.has(task.id)) return [];
    seen.add(task.id);
    return [{ review_task: task, summary: { ...task.work_summary!, review_task: task } }];
  });
  return {
    items, total: enabled ? pages?.[0]?.total ?? null : 0,
    loading: enabled && query.isPending,
    refreshing: enabled && Boolean(pages) && query.isFetching && !query.isFetchingNextPage,
    appending: enabled && query.isFetchingNextPage,
    hasMore: enabled && query.hasNextPage,
    error, appendError, retry, loadMore
  };
}
