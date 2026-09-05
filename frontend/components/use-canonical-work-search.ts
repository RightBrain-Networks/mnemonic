"use client";

import { useEffect, useState } from "react";
import { api, errorMessage, workItemPath } from "@/lib/api";
import { decodeWorkSearchPage } from "@/lib/duplicate-handling";
import type { Page, WorkSearchHit } from "@/lib/types";
import { workSearchParams } from "@/lib/work-item-search";

type SearchState = {
  key: string;
  searchedQuery: string;
  page: Page<WorkSearchHit> | null;
  searching: boolean;
  error: string;
};

const EMPTY_SEARCH: SearchState = {
  key: "",
  searchedQuery: "",
  page: null,
  searching: false,
  error: ""
};

export function useCanonicalWorkSearch({
  projectId,
  excludedWorkId,
  query,
  enabled = true,
  limit = 10
}: {
  projectId: string;
  excludedWorkId: string;
  query: string;
  enabled?: boolean;
  limit?: number;
}): Omit<SearchState, "key"> {
  const trimmedQuery = query.trim();
  const key = JSON.stringify([projectId, excludedWorkId, trimmedQuery, enabled, limit]);
  const [state, setState] = useState<SearchState>(EMPTY_SEARCH);

  useEffect(() => {
    setState({ ...EMPTY_SEARCH, key });
    if (!enabled || !trimmedQuery) return;

    const controller = new AbortController();
    const timer = setTimeout(() => {
      setState({ ...EMPTY_SEARCH, key, searchedQuery: trimmedQuery, searching: true });
      const params = workSearchParams({
        status: "all",
        sort: "updated",
        limit,
        offset: 0,
        query: trimmedQuery,
        duplicateScope: "canonical"
      });
      api<unknown>(`${workItemPath(projectId)}?${params}`, {
        signal: controller.signal
      }).then((value) => {
        if (controller.signal.aborted) return;
        const page = decodeWorkSearchPage(value, projectId, {
          duplicateScope: "canonical",
          query: trimmedQuery,
          expectedLimit: limit,
          expectedOffset: 0
        });
        setState({
          key,
          searchedQuery: trimmedQuery,
          page: {
            ...page,
            items: page.items.filter((item) => (
              item.summary.work_item.id.toLowerCase() !== excludedWorkId.toLowerCase()
            ))
          },
          searching: false,
          error: ""
        });
      }).catch((cause) => {
        if (!controller.signal.aborted) {
          setState({
            ...EMPTY_SEARCH,
            key,
            searchedQuery: trimmedQuery,
            error: errorMessage(cause)
          });
        }
      });
    }, 250);

    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [enabled, excludedWorkId, key, limit, projectId, trimmedQuery]);

  // A new scope must not render the previous request's data, error, or spinner
  // while React is waiting to run effect cleanup (including an empty query).
  return enabled && trimmedQuery && state.key === key ? state : EMPTY_SEARCH;
}
