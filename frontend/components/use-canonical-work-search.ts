"use client";

import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { errorMessage } from "@/lib/api";
import { canonicalWorkQueryOptions } from "@/lib/work-queries";
import type { Page, WorkSearchHit } from "@/lib/types";

type SearchState = {
  searchedQuery: string;
  page: Page<WorkSearchHit> | null;
  searching: boolean;
  error: string;
};

const EMPTY_SEARCH: SearchState = { searchedQuery: "", page: null, searching: false, error: "" };

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
}): SearchState {
  const trimmedQuery = query.trim();
  const options = canonicalWorkQueryOptions({ projectId, excludedWorkId, query: trimmedQuery, limit });
  const key = JSON.stringify(options.queryKey);
  const [readyKey, setReadyKey] = useState<string | null>(null);
  useEffect(() => {
    setReadyKey(null);
    if (!enabled || !trimmedQuery) return;
    const timer = setTimeout(() => setReadyKey(key), 250);
    return () => clearTimeout(timer);
  }, [enabled, key, trimmedQuery]);
  const ready = enabled && Boolean(trimmedQuery) && readyKey === key;
  const result = useQuery({ ...options, queryKey: ready ? options.queryKey : [...options.queryKey, "disabled"], enabled: ready });
  // Debouncing and disabled scopes hide cached state immediately, including the
  // render before effect cleanup when the user clears or changes the query.
  if (!ready) return EMPTY_SEARCH;
  return {
    searchedQuery: trimmedQuery,
    page: result.data ?? null,
    searching: result.isFetching,
    error: result.isError ? errorMessage(result.error) : ""
  };
}
