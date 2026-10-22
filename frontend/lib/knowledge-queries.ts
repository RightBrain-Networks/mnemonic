import { queryOptions } from "@tanstack/react-query";
import { queryKeys } from "./query-keys.ts";
import { decodeDecision, decodeDecisionContent, decodeDomain, decodeKnowledgePage, decodeScope, knowledgeJson, knowledgePath, verifyDecisionContent, type DecisionState } from "./knowledge.ts";

export const knowledgeKeys = {
  root: (projectId: string) => [...queryKeys.project(projectId), "knowledge"] as const,
  domains: (projectId: string) => [...knowledgeKeys.root(projectId), "domains"] as const
};

export async function readDomainPage(projectId: string, offset: number, signal: AbortSignal) {
  return decodeKnowledgePage(await knowledgeJson(knowledgePath(projectId, `/domains?limit=100&offset=${offset}`), { signal }),
    (value) => decodeDomain(value, projectId));
}

export function decisionsOptions(projectId: string, filters: { domainId: string; state: DecisionState | ""; q: string; offset: number }) {
  return queryOptions({
    queryKey: [...knowledgeKeys.root(projectId), "decisions", filters],
    queryFn: async ({ signal }) => {
      const params = new URLSearchParams({ limit: "25", offset: String(filters.offset) });
      if (filters.domainId) params.set("domain_id", filters.domainId);
      if (filters.state) params.set("state", filters.state);
      if (filters.q) params.set("q", filters.q);
      return decodeKnowledgePage(await knowledgeJson(knowledgePath(projectId, `/decisions?${params}`), { signal }),
        (value) => decodeDecision(value, projectId));
    },
    retry: false, refetchInterval: 30_000, refetchIntervalInBackground: false
  });
}

export function decisionContentOptions(projectId: string, id: string, revision?: number) {
  return queryOptions({
    queryKey: [...knowledgeKeys.root(projectId), "decision", id, revision ?? "current"],
    queryFn: async ({ signal }) => verifyDecisionContent(decodeDecisionContent(await knowledgeJson(knowledgePath(projectId,
      `/decisions/${encodeURIComponent(id)}${revision === undefined ? "" : `?revision=${revision}`}`), { signal }), projectId, id, revision)),
    retry: false, gcTime: 0, staleTime: revision === undefined ? 0 : Infinity,
    refetchInterval: revision === undefined ? 30_000 : false,
    refetchIntervalInBackground: false
  });
}

export function decisionHistoryOptions(projectId: string, id: string, offset: number, enabled: boolean) {
  return queryOptions({
    queryKey: [...knowledgeKeys.root(projectId), "history", id, offset], enabled,
    queryFn: async ({ signal }) => decodeKnowledgePage(await knowledgeJson(knowledgePath(projectId,
      `/decisions/${encodeURIComponent(id)}/history?offset=${offset}&limit=25`), { signal }), (value) => decodeDecision(value, projectId, id)),
    retry: false
  });
}

export function decisionScopeOptions(projectId: string, id: string, offset: number, enabled: boolean) {
  return queryOptions({
    queryKey: [...knowledgeKeys.root(projectId), "scope", id, offset], enabled,
    queryFn: async ({ signal }) => decodeKnowledgePage(await knowledgeJson(knowledgePath(projectId,
      `/decisions/${encodeURIComponent(id)}/scope?offset=${offset}&limit=25`), { signal }), (value) => decodeScope(value, projectId, id)),
    retry: false
  });
}
