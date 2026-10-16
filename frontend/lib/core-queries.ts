import { queryOptions, replaceEqualDeep, type QueryClient } from "@tanstack/react-query";
import { api } from "./api.ts";
import { queryKeys } from "./query-keys.ts";
import { loadCompleteProjectCatalog } from "./work-move.ts";
import { decodeProjectSettings, decodeReportCount } from "./job-completion-reports.ts";
import { decodeHumanAttentionPage, humanAttentionSearchParams } from "./human-gates.ts";

export function projectCatalogOptions(retainCurrentCatalog?: () => boolean) {
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
    },
    // A pending or uncertain restore must keep its mounted project panel.
    // Continue reporting read failures, but defer successful catalog changes.
    structuralSharing: (previous, next) => retainCurrentCatalog?.() && previous !== undefined
      ? previous : replaceEqualDeep(previous, next)
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

const domainRoots = {
  projects: [],
  work: ["work", "tasks"],
  settings: ["settings", "prompts", "priority-rubric", "backups"],
  attention: ["attention-count", "attention"],
  reports: ["report-count", "reports"]
} satisfies Record<CoreReadDomain, string[]>;

/** Shared invalidation boundary for local writes and live/activity notifications. */
export function invalidateCoreReads(client: QueryClient, projectId: string | null, domains: readonly CoreReadDomain[]) {
  // WebSocket events have no project identity. Inactive projects become stale
  // without starting readers; project activity targets only its own workspace.
  const roots = new Set(domains.flatMap((domain) => domainRoots[domain]));
  return client.invalidateQueries({
    predicate: ({ queryKey }) => queryKey[0] === "mnemonic" && (
      queryKey[1] === "projects" && domains.includes("projects")
      || queryKey[1] === "project" && (projectId === null || Boolean(projectId) && queryKey[2] === projectId)
        && roots.has(String(queryKey[3]))
    ),
    refetchType: "active"
  });
}
