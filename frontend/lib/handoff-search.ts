import type { StatusFilter } from "@/lib/types";

type HandoffSearchOptions = {
  status: StatusFilter;
  limit: number;
  offset: number;
  query: string;
  semantic?: boolean;
};

export function handoffSearchParams({
  status,
  limit,
  offset,
  query,
  semantic = false
}: HandoffSearchOptions): URLSearchParams {
  const params = new URLSearchParams({
    status,
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
