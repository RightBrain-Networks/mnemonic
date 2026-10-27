"use client";

import { useCallback, useMemo } from "react";
import { useInfiniteQuery, useQueryClient } from "@tanstack/react-query";
import { useReviewQueuePages } from "@/components/use-review-queue-pages";
import { combineQueuePages } from "@/lib/combined-work-queue";
import { useFailedReadRetry } from "@/components/use-failed-read-retry";
import { errorMessage } from "@/lib/api";
import { isFlatWorkSearch } from "@/lib/work-item-search";
import { mergeWorkPages, type WorkQueueItem } from "@/lib/work-queue";
import { workQueueQueryOptions, type WorkQueueFilters } from "@/lib/work-queries";

export type WorkQueuePagesInput = WorkQueueFilters & { enabled: boolean };

export type WorkQueuePages = {
  viewKey: string;
  flatSearch: boolean;
  items: WorkQueueItem[];
  total: number | null;
  loading: boolean;
  refreshing: boolean;
  appending: boolean;
  error: string;
  appendError: string;
  hasMore: boolean;
  loadMore: () => void;
  retry: () => void;
  retryAppend: () => void;
};

const EMPTY_ITEMS: WorkQueueItem[] = [];

function useImplementationQueuePages({ enabled, ...filters }: WorkQueuePagesInput): WorkQueuePages {
  const client = useQueryClient();
  const options = workQueueQueryOptions(client, filters);
  const viewKey = JSON.stringify(options.queryKey);
  // Moving the observer also aborts a read when this surface becomes disabled.
  const query = useInfiniteQuery({ ...options, queryKey: enabled ? options.queryKey : [...options.queryKey, "disabled"], enabled });
  const visible = enabled ? query.data : undefined;
  const merged = useMemo(() => {
    if (!visible) return null;
    const pages = visible.pages.flatMap((chunk) => chunk.pages);
    const result = mergeWorkPages(pages);
    if (visible.pages.at(-1)?.appended) result.total = pages.at(-1)?.total ?? result.total;
    return result;
  }, [visible]);
  const error = enabled && query.isError && !query.isFetchNextPageError ? errorMessage(query.error) : "";
  const appendError = enabled && query.isFetchNextPageError ? errorMessage(query.error) : "";
  const retry = useCallback(() => { if (enabled) void query.refetch(); }, [enabled, query.refetch]);
  const loadMore = useCallback(() => {
    if (enabled && query.hasNextPage && !query.isFetching) void query.fetchNextPage({ cancelRefetch: false });
  }, [enabled, query.hasNextPage, query.isFetching, query.fetchNextPage]);
  const automaticRetry = enabled && !(filters.search.trim() && filters.semantic);
  useFailedReadRetry({ scope: `queue:${viewKey}`, failed: Boolean(error), busy: query.isFetching, enabled: automaticRetry, retry });
  useFailedReadRetry({ scope: `queue-append:${viewKey}`, failed: Boolean(appendError), busy: query.isFetching, enabled: automaticRetry, retry: loadMore });
  return {
    viewKey,
    flatSearch: isFlatWorkSearch({ query: filters.search, duplicateScope: filters.duplicateScope, canonicalWorkItemId: filters.canonicalWorkItemId }),
    items: merged?.items ?? EMPTY_ITEMS,
    total: merged?.total ?? null,
    loading: enabled && query.isPending,
    refreshing: enabled && Boolean(visible) && query.isFetching && !query.isFetchingNextPage,
    appending: enabled && query.isFetchingNextPage,
    error,
    appendError,
    hasMore: enabled && query.hasNextPage,
    loadMore,
    retry,
    retryAppend: loadMore
  };
}

export function useWorkQueuePages(input: WorkQueuePagesInput): WorkQueuePages {
  const work = useImplementationQueuePages(input);
  const reviewsEnabled = input.enabled && input.duplicateScope === "canonical" && !input.canonicalWorkItemId;
  const reviews = useReviewQueuePages(input, reviewsEnabled);
  const items = combineQueuePages(work.items, reviews.items, { sort: input.sort,
    pins: input.pinnedWorkItemIds, workHasMore: work.hasMore, reviewsHaveMore: reviews.hasMore,
    semanticSearch: Boolean(input.search.trim() && input.semantic) });
  const retry = () => { work.retry(); reviews.retry(); };
  const loadMore = () => { work.loadMore(); reviews.loadMore(); };
  return { ...work, items, total: work.total === null || reviews.total === null ? null : work.total + reviews.total,
    loading: work.loading || reviews.loading, refreshing: work.refreshing || reviews.refreshing,
    appending: work.appending || reviews.appending,
    appendError: [work.appendError, reviews.appendError].filter(Boolean).join(" "),
    hasMore: work.hasMore || reviews.hasMore, error: [work.error, reviews.error].filter(Boolean).join(" "),
    loadMore, retry, retryAppend: loadMore };
}
