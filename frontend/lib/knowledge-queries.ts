import { decodeAssessment, decodeAssessmentDetail, decodeDisposition, decodeImpact, type ImpactKind } from "./knowledge-impact.ts";
import { queryOptions, type QueryClient } from "@tanstack/react-query";
import { queryKeys } from "./query-keys.ts";
import { decodeDecisionChange } from "./decision-changes.ts";
import { decodeDecision, decodeDecisionContent, decodeDomain, decodeKnowledgePage, decodeScope, knowledgeJson, knowledgePath, verifyDecisionContent, type DecisionState } from "./knowledge.ts";

export const knowledgeKeys = {
  root: (projectId: string) => [...queryKeys.project(projectId), "knowledge"] as const,
  domains: (projectId: string) => [...knowledgeKeys.root(projectId), "domains"] as const
};

export function invalidateKnowledgeReads(client: QueryClient, projectId: string | null) {
  // Live work notifications intentionally carry no project identity. A moved
  // follow-up can display review scope owned by several other projects.
  return client.invalidateQueries({
    predicate: ({ queryKey }) => queryKey[0] === "mnemonic" && queryKey[1] === "project"
      && (projectId === null || queryKey[2] === projectId) && queryKey[3] === "knowledge",
    refetchType: "active"
  });
}

export function decisionChangeOptions(projectId: string, changeId: string) {
  return queryOptions({
    queryKey: [...knowledgeKeys.root(projectId), "change", changeId],
    queryFn: async ({ signal }) => decodeDecisionChange(await knowledgeJson(knowledgePath(projectId,
      `/changes/${encodeURIComponent(changeId)}`), { signal }), projectId, changeId),
    retry: false, staleTime: 0, refetchInterval: 30_000, refetchIntervalInBackground: false
  });
}

export function changeAssessmentsOptions(projectId: string, changeId: string, scopeVersion: number, offset: number) {
  return queryOptions({
    queryKey: [...knowledgeKeys.root(projectId), "change-assessments", changeId, scopeVersion, offset],
    queryFn: async ({ signal }) => decodeKnowledgePage(await knowledgeJson(knowledgePath(projectId,
      `/assessments?change_id=${encodeURIComponent(changeId)}&offset=${offset}&limit=25`), { signal }),
    (value) => decodeAssessment(value, projectId)), retry: false
  });
}

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

export function decisionImpactOptions(projectId: string, id: string, revision: number, offset: number, kind: ImpactKind = "code-impact") {
  return queryOptions({
    queryKey: [...knowledgeKeys.root(projectId), "impact", id, revision, offset, kind],
    queryFn: async ({ signal }) => decodeImpact(await knowledgeJson(knowledgePath(projectId,
      `/decisions/${encodeURIComponent(id)}/impact?revision=${revision}&offset=${offset}&limit=25&kind=${kind}`), { signal }), projectId, `${id}-${revision}`, kind), retry: false
  });
}
export function assessmentsOptions(projectId: string, decisionId: string, offset: number) {
  return queryOptions({
    queryKey: [...knowledgeKeys.root(projectId), "assessments", decisionId, offset],
    queryFn: async ({ signal }) => decodeKnowledgePage(await knowledgeJson(knowledgePath(projectId,
      `/assessments?decision_id=${encodeURIComponent(decisionId)}&offset=${offset}&limit=25`), { signal }), (value) => decodeAssessment(value, projectId)), retry: false
  });
}
export function assessmentOptions(projectId: string, id: string, offset: number, kind: ImpactKind = "code-impact") {
  return queryOptions({
    queryKey: [...knowledgeKeys.root(projectId), "assessment", id, offset, kind],
    queryFn: async ({ signal }) => decodeAssessmentDetail(await knowledgeJson(knowledgePath(projectId,
      `/assessments/${id}?offset=${offset}&limit=25&kind=${kind}`), { signal }), projectId, id, kind), retry: false
  });
}
export function dispositionHistoryOptions(projectId: string, id: string, offset: number, enabled: boolean) {
  return queryOptions({
    queryKey: [...knowledgeKeys.root(projectId), "dispositions", id, offset], enabled,
    queryFn: async ({ signal }) => decodeKnowledgePage(await knowledgeJson(knowledgePath(projectId,
      `/assessments/${id}/dispositions?offset=${offset}&limit=25`), { signal }), (value) => decodeDisposition(value, projectId, id)), retry: false
  });
}
export function commitLookupOptions(projectId: string, repository: string, commitHash: string, offset: number, enabled: boolean) {
  return queryOptions({
    queryKey: [...knowledgeKeys.root(projectId), "commit", repository, commitHash, offset], enabled,
    queryFn: async ({ signal }) => decodeKnowledgePage(await knowledgeJson(knowledgePath(projectId,
      `/commits?${new URLSearchParams({ repository, commit_hash: commitHash, offset: String(offset), limit: "25" })}`), { signal }), (value) => decodeScope(value, projectId)), retry: false
  });
}

export function completionDomainsOptions(projectId: string) {
  return queryOptions({ queryKey: [...knowledgeKeys.root(projectId), "completion-domains"],
    queryFn: ({ signal }) => readDomainPage(projectId, 0, signal), staleTime: 0, retry: false });
}
