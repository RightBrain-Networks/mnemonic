import type { StatusFilter } from "@/lib/types";

type WorkSearchOptions = {
  status: StatusFilter;
  limit: number;
  offset: number;
  query: string;
  semantic?: boolean;
};

export function workSearchParams({
  status,
  limit,
  offset,
  query,
  semantic = false
}: WorkSearchOptions): URLSearchParams {
  const params = new URLSearchParams({
    status,
    view: "all",
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
