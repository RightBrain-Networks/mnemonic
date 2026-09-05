"use client";

import { useFailedReadRetry } from "@/components/use-failed-read-retry";
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { api, errorMessage, workItemPath } from "@/lib/api";
import { decodeWorkSearchPage } from "@/lib/duplicate-handling";
import { decodeHierarchyPage } from "@/lib/hierarchy-presentation";
import type { DuplicateScope, Page, StatusFilter, WorkSort } from "@/lib/types";
import { isFlatWorkSearch, workSearchParams } from "@/lib/work-item-search";
import {
  WORK_PAGE_SIZE,
  appendWorkPage,
  hasMoreWork,
  loadedOffsets,
  mergeWorkPages,
  type MergedWorkPages,
  type WorkQueueItem
} from "@/lib/work-queue";

export type WorkQueuePagesInput = {
  enabled: boolean;
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
  refresh: number;
};

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

type LoadedView = MergedWorkPages<WorkQueueItem> & { viewKey: string };
type ViewFailure = { viewKey: string; message: string };

const EMPTY_ITEMS: WorkQueueItem[] = [];

export function useWorkQueuePages({
  enabled,
  projectId,
  status,
  sort,
  search,
  semantic,
  duplicateScope,
  canonicalWorkItemId,
  tag,
  sourceClient,
  sourceSessionId,
  refresh
}: WorkQueuePagesInput): WorkQueuePages {
  const viewKey = JSON.stringify([
    projectId,
    status,
    sort,
    search,
    semantic,
    duplicateScope,
    canonicalWorkItemId,
    tag,
    sourceClient,
    sourceSessionId
  ]);
  const flatSearch = isFlatWorkSearch({ query: search, duplicateScope, canonicalWorkItemId });
  const [data, setData] = useState<LoadedView | null>(null);
  const [fetching, setFetching] = useState(false);
  const [appending, setAppending] = useState(false);
  const [failure, setFailure] = useState<ViewFailure | null>(null);
  const [appendFailure, setAppendFailure] = useState<ViewFailure | null>(null);
  const [retryCount, setRetryCount] = useState(0);
  const dataRef = useRef<LoadedView | null>(null);
  const fetchingRef = useRef(false);
  const generationRef = useRef(0);
  const appendControllerRef = useRef<AbortController | null>(null);

  // Layout timing keeps the ref current before any child's passive effect asks for the next page.
  useLayoutEffect(() => { dataRef.current = data; }, [data]);

  const fetchPage = useCallback(async (
    offset: number,
    signal: AbortSignal
  ): Promise<Page<WorkQueueItem>> => {
    const params = workSearchParams({
      status,
      sort,
      limit: WORK_PAGE_SIZE,
      offset,
      query: search,
      semantic,
      tag,
      sourceClient,
      sourceSessionId,
      duplicateScope,
      ...(canonicalWorkItemId ? { canonicalWorkItemId } : {})
    });
    const value = await api<unknown>(`${workItemPath(projectId)}?${params}`, { signal });
    return flatSearch
      ? decodeWorkSearchPage(value, projectId, {
        duplicateScope,
        canonicalWorkItemId: canonicalWorkItemId || undefined,
        query: search,
        expectedLimit: WORK_PAGE_SIZE,
        expectedOffset: offset
      })
      : decodeHierarchyPage(value, projectId, WORK_PAGE_SIZE, offset);
  }, [
    canonicalWorkItemId,
    duplicateScope,
    flatSearch,
    projectId,
    search,
    semantic,
    sort,
    sourceClient,
    sourceSessionId,
    status,
    tag
  ]);

  const abortAppend = useCallback(() => {
    const controller = appendControllerRef.current;
    if (!controller) return;
    controller.abort();
    appendControllerRef.current = null;
    setAppending(false);
  }, []);

  useEffect(() => {
    generationRef.current += 1;
    abortAppend();
    setAppendFailure(null);
    if (!enabled) {
      fetchingRef.current = false;
      setFetching(false);
      setFailure(null);
      setData(null);
      return;
    }
    const generation = generationRef.current;
    const controller = new AbortController();
    const current = dataRef.current;
    const offsets = current?.viewKey === viewKey
      ? loadedOffsets(current.loaded, WORK_PAGE_SIZE)
      : [0];
    const requestedViewKey = viewKey;
    fetchingRef.current = true;
    setFetching(true);
    setFailure(null);
    Promise.all(offsets.map((offset) => fetchPage(offset, controller.signal)))
      .then((pages) => {
        if (controller.signal.aborted || generationRef.current !== generation) return;
        setData({ viewKey: requestedViewKey, ...mergeWorkPages(pages) });
      })
      .catch((error) => {
        if (controller.signal.aborted || generationRef.current !== generation) return;
        setFailure({ viewKey: requestedViewKey, message: errorMessage(error) });
      })
      .finally(() => {
        if (controller.signal.aborted || generationRef.current !== generation) return;
        fetchingRef.current = false;
        setFetching(false);
      });
    return () => controller.abort();
  }, [abortAppend, enabled, fetchPage, refresh, retryCount, viewKey]);

  useEffect(() => () => {
    generationRef.current += 1;
    appendControllerRef.current?.abort();
    appendControllerRef.current = null;
  }, []);

  const loadMore = useCallback(() => {
    const current = dataRef.current;
    if (!enabled || !current || current.viewKey !== viewKey) return;
    if (fetchingRef.current || appendControllerRef.current) return;
    if (!hasMoreWork(current.loaded, current.total)) return;
    const offset = current.loaded;
    const generation = generationRef.current;
    const controller = new AbortController();
    const requestedViewKey = viewKey;
    appendControllerRef.current = controller;
    setAppending(true);
    setAppendFailure(null);
    fetchPage(offset, controller.signal)
      .then((page) => {
        if (controller.signal.aborted || generationRef.current !== generation) return;
        setData((previous) => (
          previous && previous.viewKey === requestedViewKey && previous.loaded === offset
            ? { viewKey: requestedViewKey, ...appendWorkPage(previous, page) }
            : previous
        ));
      })
      .catch((error) => {
        if (controller.signal.aborted || generationRef.current !== generation) return;
        setAppendFailure({ viewKey: requestedViewKey, message: errorMessage(error) });
      })
      .finally(() => {
        if (appendControllerRef.current !== controller) return;
        appendControllerRef.current = null;
        setAppending(false);
      });
  }, [enabled, fetchPage, viewKey]);

  const retry = useCallback(() => setRetryCount((value) => value + 1), []);
  const retryAppend = useCallback(() => {
    setAppendFailure(null);
    loadMore();
  }, [loadMore]);

  const visible = data?.viewKey === viewKey ? data : null;
  const error = failure?.viewKey === viewKey ? failure.message : "";
  const appendError = appendFailure?.viewKey === viewKey ? appendFailure.message : "";
  const total = visible?.total ?? null;
  useFailedReadRetry({ scope: `queue:${viewKey}`, failed: Boolean(error), busy: fetching, enabled, retry });
  useFailedReadRetry({ scope: `queue-append:${viewKey}`, failed: Boolean(appendError), busy: appending || fetching, enabled, retry: retryAppend });
  return {
    viewKey,
    flatSearch,
    items: visible?.items ?? EMPTY_ITEMS,
    total,
    loading: enabled && !visible && (fetching || !error),
    refreshing: fetching && visible !== null,
    appending,
    error,
    appendError,
    hasMore: hasMoreWork(visible?.loaded ?? 0, total),
    loadMore,
    retry,
    retryAppend
  };
}
