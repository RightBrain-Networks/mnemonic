import type { StatusFilter, WorkSort } from "@/lib/types";

export const HIERARCHY_FILTER_DEBOUNCE_MS = 300;

export type HierarchyFilterValues = {
  tag: string;
  sourceClient: string;
  sourceSessionId: string;
};

export type DebounceScheduler = (
  callback: () => void,
  delay: number
) => () => void;

const defaultDebounceScheduler: DebounceScheduler = (callback, delay) => {
  const timer = setTimeout(callback, delay);
  return () => clearTimeout(timer);
};

export function scheduleHierarchyFilterCommit(
  input: HierarchyFilterValues,
  current: HierarchyFilterValues,
  commit: (next: HierarchyFilterValues) => void,
  schedule: DebounceScheduler = defaultDebounceScheduler
): () => void {
  const next = {
    tag: input.tag.trim(),
    sourceClient: input.sourceClient.trim(),
    sourceSessionId: input.sourceSessionId.trim()
  };
  return schedule(() => {
    if (
      next.tag === current.tag
      && next.sourceClient === current.sourceClient
      && next.sourceSessionId === current.sourceSessionId
    ) return;
    commit(next);
  }, HIERARCHY_FILTER_DEBOUNCE_MS);
}

type WorkSearchOptions = {
  status: StatusFilter;
  sort: WorkSort;
  limit: number;
  offset: number;
  query: string;
  semantic?: boolean;
  tag?: string;
  sourceClient?: string;
  sourceSessionId?: string;
};

function addHierarchyFilters(
  params: URLSearchParams,
  input: Pick<WorkSearchOptions, "tag" | "sourceClient" | "sourceSessionId">
): void {
  const tag = input.tag?.trim();
  const sourceClient = input.sourceClient?.trim();
  const sourceSessionId = input.sourceSessionId?.trim();
  if (tag) params.set("tag", tag);
  if (sourceClient) params.set("source_client", sourceClient);
  if (sourceSessionId) params.set("source_session_id", sourceSessionId);
}

export function workSearchParams({
  status,
  sort,
  limit,
  offset,
  query,
  semantic = false,
  tag,
  sourceClient,
  sourceSessionId
}: WorkSearchOptions): URLSearchParams {
  const params = new URLSearchParams({
    status,
    sort,
    view: query.trim() ? "full" : "roots",
    limit: String(limit),
    offset: String(offset)
  });
  const trimmedQuery = query.trim();
  if (trimmedQuery) {
    params.set("q", trimmedQuery);
    if (semantic) params.set("semantic", "true");
  }
  addHierarchyFilters(params, { tag, sourceClient, sourceSessionId });
  return params;
}

export function childSearchParams({
  status,
  sort,
  limit,
  offset,
  tag,
  sourceClient,
  sourceSessionId
}: Pick<
  WorkSearchOptions,
  "status" | "sort" | "limit" | "offset" | "tag" | "sourceClient" | "sourceSessionId"
>): URLSearchParams {
  const params = new URLSearchParams({ status, sort, limit: String(limit), offset: String(offset) });
  addHierarchyFilters(params, { tag, sourceClient, sourceSessionId });
  return params;
}
