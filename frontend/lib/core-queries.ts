import { queryOptions, type QueryClient, type QueryKey } from "@tanstack/react-query";
import { api } from "./api.ts";
import { queryKeys } from "./query-keys.ts";
import { loadCompleteProjectCatalog } from "./work-move.ts";
import { decodeProjectSettings, decodeReportCount } from "./job-completion-reports.ts";
import { decodeHumanAttentionPage, humanAttentionSearchParams } from "./human-gates.ts";

export function projectCatalogOptions() {
  return queryOptions({
    queryKey: queryKeys.projects,
    queryFn: async ({ signal }) => {
      const projects = await loadCompleteProjectCatalog(
        (offset) => api<unknown>(`/projects?limit=100&offset=${offset}`, { signal }),
        () => !signal.aborted
      );
      signal.throwIfAborted();
      if (!projects) throw new Error("The project catalog read was interrupted.");
      return projects.sort((a, b) => a.name.localeCompare(b.name) || a.id.localeCompare(b.id));
    }
  });
}

export function projectSettingsOptions(projectId: string) {
  return queryOptions({
    queryKey: queryKeys.settings(projectId),
    queryFn: async ({ signal }) => decodeProjectSettings(await api<unknown>(
      `/projects/${encodeURIComponent(projectId)}/settings`, { signal }
    ), projectId)
  });
}

export function attentionCountOptions(projectId: string) {
  return queryOptions({
    queryKey: queryKeys.attentionCount(projectId),
    queryFn: async ({ signal }) => decodeHumanAttentionPage(await api<unknown>(
      `/projects/${encodeURIComponent(projectId)}/human-attention?${humanAttentionSearchParams({ limit: 0 })}`,
      { signal }
    ), projectId, { limit: 0 })
  });
}

export function reportCountOptions(projectId: string) {
  return queryOptions({
    queryKey: queryKeys.reportCount(projectId),
    queryFn: async ({ signal }) => decodeReportCount(await api<unknown>(
      `/projects/${encodeURIComponent(projectId)}/job-completion-reports/count`, { signal }
    ), projectId)
  });
}

export type CoreReadDomain = "projects" | "settings" | "work" | "attention" | "reports";

/** Temporary boundary for live events and confirmed writes during migration 5–7. */
export function invalidateCoreReads(client: QueryClient, projectId: string | null, domains: readonly CoreReadDomain[]) {
  if (projectId === null) {
    // WebSocket events carry a domain but no project identity. Keep inactive
    // projects stale so returning to a cached picker still sees those writes.
    const roots = new Set(domains.flatMap((domain) => {
      switch (domain) {
        case "work": return ["work", "tasks"];
        case "settings": return ["settings"];
        case "attention": return ["attention-count"];
        case "reports": return ["report-count"];
        default: return [];
      }
    }));
    return client.invalidateQueries({
      predicate: ({ queryKey }) => queryKey[0] === "mnemonic" && (
        queryKey[1] === "projects" && domains.includes("projects")
        || queryKey[1] === "project" && roots.has(String(queryKey[3]))
      ),
      refetchType: "active"
    });
  }
  const keys = domains.flatMap<QueryKey>((domain) => {
    if (domain === "projects") return [queryKeys.projects];
    if (!projectId) return [];
    switch (domain) {
      case "work": return [queryKeys.work(projectId), queryKeys.tasks(projectId)];
      case "settings": return [queryKeys.settings(projectId)];
      case "attention": return [queryKeys.attentionCount(projectId)];
      case "reports": return [queryKeys.reportCount(projectId)];
    }
  });
  return Promise.all(keys.map((queryKey) => client.invalidateQueries({ queryKey })));
}
