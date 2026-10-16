import { queryOptions } from "@tanstack/react-query";
import { api } from "./api.ts";
import { decodeDuplicateSuggestionPage } from "./duplicate-suggestions.ts";
import { queryKeys } from "./query-keys.ts";
import type { DuplicateSuggestionInput } from "./types.ts";

/** Explicit draft comparison only: never infer on mount, focus or invalidation. */
export function duplicateSuggestionOptions(projectId: string, request: DuplicateSuggestionInput | null) {
  return queryOptions({
    queryKey: request ? [...queryKeys.project(projectId), "duplicate-suggestions", request] as const
      : ["mnemonic", "disabled", "duplicate-suggestions"] as const,
    enabled: false,
    staleTime: 0,
    gcTime: 0,
    retry: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    queryFn: async ({ signal }) => {
      if (!request) throw new Error("No comparison selected.");
      return decodeDuplicateSuggestionPage(await api<unknown>(
        `/projects/${encodeURIComponent(projectId)}/duplicate-suggestions`,
        { method: "POST", body: JSON.stringify(request), signal }
      ), request);
    }
  });
}
