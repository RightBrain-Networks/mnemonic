import { CancelledError, queryOptions, type QueryClient } from "@tanstack/react-query";
import { api, workItemPath } from "./api.ts";
import { decodeWorkContext, decodeWorkItemDetail } from "./duplicate-handling.ts";
import { decodeCheckpointPage } from "./checkpoint-codecs.ts";
import { decodeProjectSettings } from "./job-completion-reports.ts";
import { decodeHierarchyPage } from "./hierarchy-presentation.ts";
import { childSearchParams } from "./work-item-search.ts";

export const workDetailKeys = {
  project: (projectId: string) => ["mnemonic", "project", projectId, "work-detail"] as const,
  work: (projectId: string, workId: string) => [...workDetailKeys.project(projectId), workId] as const,
  context: (projectId: string, workId: string, recentLimit = 5, recentEventLimit = 10) =>
    [...workDetailKeys.work(projectId, workId), "context", { recentLimit, recentEventLimit }] as const,
  detail: (projectId: string, workId: string) => [...workDetailKeys.work(projectId, workId), "detail"] as const,
  checkpoints: (projectId: string, workId: string, limit: number, offset: number) =>
    [...workDetailKeys.work(projectId, workId), "checkpoints", { order: "newest", limit, offset }] as const,
  settings: (projectId: string, workId: string) => [...workDetailKeys.work(projectId, workId), "settings"] as const,
  children: (projectId: string, workId: string, params: string) =>
    [...workDetailKeys.work(projectId, workId), "children", params] as const
};

export function workContextOptions(projectId: string, workId: string, recentLimit = 5, recentEventLimit = 10) {
  return queryOptions({
    queryKey: workDetailKeys.context(projectId, workId, recentLimit, recentEventLimit),
    queryFn: async ({ signal }) => decodeWorkContext(await api<unknown>(
      `${workItemPath(projectId, workId)}/context?recent_limit=${recentLimit}&recent_event_limit=${recentEventLimit}`,
      { signal }
    ), projectId, workId)
  });
}

/** Reconciliation must observe a request begun after the confirmed write. */
export async function fetchFreshWorkContext(client: QueryClient, projectId: string, workId: string, recentLimit = 5, recentEventLimit = 10) {
  const options = workContextOptions(projectId, workId, recentLimit, recentEventLimit);
  await client.cancelQueries({ queryKey: options.queryKey, exact: true });
  let signal: AbortSignal | undefined;
  const context = await client.fetchQuery({ ...options, staleTime: 0, queryFn: (request) => {
    signal = request.signal;
    return options.queryFn!(request);
  } });
  // Query can resolve a cancelled refetch with retained cache data. Placement
  // verification must only accept this request's completed transport result.
  if (!signal || signal.aborted) throw new CancelledError({ revert: true });
  return context;
}

export function workDetailOptions(projectId: string, workId: string) {
  return queryOptions({
    queryKey: workDetailKeys.detail(projectId, workId),
    queryFn: async ({ signal }) => decodeWorkItemDetail(
      await api<unknown>(workItemPath(projectId, workId), { signal }), projectId, workId
    )
  });
}

export function workCheckpointsOptions(projectId: string, workId: string, limit: number, offset: number) {
  return queryOptions({
    queryKey: workDetailKeys.checkpoints(projectId, workId, limit, offset),
    queryFn: async ({ signal }) => decodeCheckpointPage(await api<unknown>(
      `${workItemPath(projectId, workId)}/checkpoints?order=newest&limit=${limit}&offset=${offset}`, { signal }
    ), workId, { limit, offset })
  });
}

export function workSettingsOptions(projectId: string, workId: string) {
  return queryOptions({
    queryKey: workDetailKeys.settings(projectId, workId),
    queryFn: async ({ signal }) => decodeProjectSettings(await api<unknown>(
      `/projects/${encodeURIComponent(projectId)}/settings?work_item_id=${encodeURIComponent(workId)}`, { signal }
    ), projectId)
  });
}

/** Policy decisions need a read begun for the action, including during prompt refresh. */
export async function fetchFreshWorkSettings(client: QueryClient, projectId: string, workId: string) {
  const options = workSettingsOptions(projectId, workId);
  await client.cancelQueries({ queryKey: options.queryKey, exact: true });
  let signal: AbortSignal | undefined;
  const settings = await client.fetchQuery({ ...options, staleTime: 0, queryFn: (request) => {
    signal = request.signal;
    return options.queryFn!(request);
  } });
  if (!signal || signal.aborted) throw new CancelledError({ revert: true });
  return settings;
}

/** Settings changes refresh rendered instructions without reloading unrelated detail panels. */
export function invalidateWorkSettingsReads(client: QueryClient, projectId: string | null) {
  return client.invalidateQueries({
    predicate: ({ queryKey }) => queryKey[0] === "mnemonic" && queryKey[1] === "project"
      && (projectId === null || queryKey[2] === projectId) && queryKey[3] === "work-detail"
      && queryKey[5] === "settings",
    refetchType: "active"
  });
}

export function workChildrenOptions(projectId: string, workId: string, filters: Parameters<typeof childSearchParams>[0] & { limit: number; offset: number }) {
  const params = childSearchParams(filters).toString();
  return queryOptions({
    queryKey: workDetailKeys.children(projectId, workId, params),
    // Reopening a branch refreshes its child metadata without refetching roots.
    staleTime: 0,
    queryFn: async ({ signal }) => decodeHierarchyPage(await api<unknown>(
      `${workItemPath(projectId, workId)}/children?${params}`, { signal }
    ), projectId, filters.limit, filters.offset)
  });
}

/** Live events do not identify one work item; inactive projects are only marked stale. */
export function invalidateWorkDetailReads(client: QueryClient, projectId: string | null) {
  return client.invalidateQueries({
    predicate: ({ queryKey }) => queryKey[0] === "mnemonic" && queryKey[1] === "project"
      && queryKey[3] === "work-detail"
      && (projectId === null || queryKey[2] === projectId
        || queryKey[5] === "report" && queryKey[6] === projectId),
    refetchType: "active"
  }, { cancelRefetch: false });
}
