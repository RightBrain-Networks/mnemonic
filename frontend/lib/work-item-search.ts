import type { StatusFilter, WorkSort } from "@/lib/types";

type WorkSearchOptions = {
  status: StatusFilter;
  sort: WorkSort;
  limit: number;
  offset: number;
  query: string;
  semantic?: boolean;
};

export function workSearchParams({
  status,
  sort,
  limit,
  offset,
  query,
  semantic = false
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
  return params;
}

export function childSearchParams({
  status,
  sort,
  limit,
  offset
}: Pick<WorkSearchOptions, "status" | "sort" | "limit" | "offset">): URLSearchParams {
  return new URLSearchParams({ status, sort, limit: String(limit), offset: String(offset) });
}
