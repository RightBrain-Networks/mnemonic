import { CancelledError, infiniteQueryOptions, queryOptions, type QueryClient } from "@tanstack/react-query";
import { api, detailMessage } from "./api.ts";
import { backupPath, decodeBackupJob, decodeBackups, type BackupJob } from "./backups.ts";
import { readBoundedJson } from "./bounded-json.ts";
import { decodeHumanAttentionPage, humanAttentionSearchParams } from "./human-gates.ts";
import { decodeReportPage } from "./job-completion-reports.ts";
import { decodePriorityRubric } from "./priority-rubric.ts";
import { decodePromptDetail, decodePromptLibrary, promptPath, renderedPrompt, type PromptId } from "./prompts.ts";
import { queryKeys } from "./query-keys.ts";
import type { JobReportPage } from "./types.ts";

export const ATTENTION_PAGE_SIZE = 30;
export const secondaryKeys = {
  prompts: (projectId: string) => [...queryKeys.project(projectId), "prompts"] as const,
  prompt: (projectId: string, promptId: string) => [...secondaryKeys.prompts(projectId), promptId] as const,
  renderedPrompt: (projectId: string, promptId: string, workItemId: string, codeReviewId?: string) =>
    [...secondaryKeys.prompt(projectId, promptId), "render", { workItemId, codeReviewId: codeReviewId ?? null }] as const,
  priorityRubric: (projectId: string) => [...queryKeys.project(projectId), "priority-rubric"] as const,
  backups: (projectId: string) => [...queryKeys.project(projectId), "backups"] as const,
  backupCatalog: (projectId: string) => [...secondaryKeys.backups(projectId), "catalog"] as const,
  backupJob: (projectId: string, jobId: string) => [...secondaryKeys.backups(projectId), "job", jobId] as const,
  attention: (projectId: string) => [...queryKeys.project(projectId), "attention"] as const,
  attentionPage: (projectId: string, workItemId: string | undefined, cursor: string | null) =>
    [...secondaryKeys.attention(projectId), { workItemId: workItemId ?? null, limit: ATTENTION_PAGE_SIZE, cursor }] as const,
  reports: (projectId: string) => [...queryKeys.project(projectId), "reports"] as const,
  reportList: (projectId: string) => [...secondaryKeys.reports(projectId), { dismissal: "undismissed", limit: 20 }] as const
};

export function promptLibraryOptions(projectId: string) {
  return queryOptions({
    queryKey: secondaryKeys.prompts(projectId),
    queryFn: async ({ signal }) => decodePromptLibrary(await api<unknown>(promptPath(projectId), { signal }))
  });
}

export function promptDetailOptions(projectId: string, promptId: PromptId) {
  return queryOptions({
    queryKey: secondaryKeys.prompt(projectId, promptId), staleTime: 0,
    queryFn: async ({ signal }) => decodePromptDetail(await api<unknown>(promptPath(projectId, promptId), { signal }), promptId)
  });
}

/** Action rendering has no automatic observer or retry and retains no inactive body. */
export function renderedPromptOptions(projectId: string, promptId: PromptId, workItemId: string, codeReviewId?: string) {
  return queryOptions({
    queryKey: secondaryKeys.renderedPrompt(projectId, promptId, workItemId, codeReviewId),
    enabled: false,
    staleTime: 0,
    gcTime: 0,
    retry: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    queryFn: ({ signal }) => renderedPrompt(projectId, promptId, workItemId, codeReviewId, signal)
  });
}

/** Clipboard actions accept only a new, uncancelled rendering for this exact scope. */
export async function fetchFreshRenderedPrompt(client: QueryClient, projectId: string, promptId: PromptId, workItemId: string, codeReviewId?: string) {
  const options = renderedPromptOptions(projectId, promptId, workItemId, codeReviewId);
  // Keep cancellation and dispatch together; a concurrent action may supersede
  // this one, but cannot make its older cached instructions count as fresh.
  void client.cancelQueries({ queryKey: options.queryKey, exact: true });
  let signal: AbortSignal | undefined;
  const content = await client.fetchQuery({ ...options, queryFn: (request) => {
    signal = request.signal;
    return options.queryFn!(request);
  } });
  if (!signal || signal.aborted) throw new CancelledError({ revert: true });
  return content;
}

export function priorityRubricOptions(projectId: string) {
  return queryOptions({
    queryKey: secondaryKeys.priorityRubric(projectId),
    queryFn: async ({ signal }) => decodePriorityRubric(await api<unknown>(
      `/projects/${encodeURIComponent(projectId)}/priority-rubric`, { signal }
    ), projectId)
  });
}

async function backupRead(path: string, signal: AbortSignal) {
  const response = await fetch(path, { cache: "no-store", signal });
  const value = await readBoundedJson(response, 4 * 1024 * 1024);
  if (!response.ok) {
    const failure = value as { detail?: unknown; error?: unknown } | null;
    throw new Error(detailMessage(failure?.detail ?? failure?.error).message || "Unable to read project backups.");
  }
  return value;
}
export function backupCatalogOptions(projectId: string) {
  return queryOptions({
    queryKey: secondaryKeys.backupCatalog(projectId),
    queryFn: async ({ signal }) => decodeBackups(await backupRead(`${backupPath(projectId)}/backups`, signal), projectId)
  });
}
export function backupJobActive(job: BackupJob | undefined) {
  return job?.state === "pending" || job?.state === "running";
}
export function backupJobOptions(projectId: string, jobId: string) {
  return queryOptions({
    queryKey: secondaryKeys.backupJob(projectId, jobId), staleTime: 0,
    queryFn: async ({ signal }) => decodeBackupJob(await backupRead(
      `${backupPath(projectId)}/backup-jobs/${encodeURIComponent(jobId)}`,
      AbortSignal.any([signal, AbortSignal.timeout(30_000)])
    ), projectId, jobId),
    refetchInterval: (query) => !query.state.error && backupJobActive(query.state.data) ? 1_000 : false,
    refetchIntervalInBackground: false
  });
}

export function humanAttentionOptions(projectId: string, workItemId: string | undefined, cursor: string | null) {
  return queryOptions({
    queryKey: secondaryKeys.attentionPage(projectId, workItemId, cursor),
    queryFn: async ({ signal }) => decodeHumanAttentionPage(await api<unknown>(
      `/projects/${encodeURIComponent(projectId)}/human-attention?${humanAttentionSearchParams({ workItemId, limit: ATTENTION_PAGE_SIZE, cursor })}`,
      { signal }
    ), projectId, { workItemId, limit: ATTENTION_PAGE_SIZE })
  });
}

export function jobReportListOptions(projectId: string) {
  return infiniteQueryOptions({
    queryKey: secondaryKeys.reportList(projectId),
    initialPageParam: null as { previous: JobReportPage; pages: JobReportPage[] } | null,
    queryFn: async ({ pageParam, signal }) => {
      const params = new URLSearchParams({ dismissal: "undismissed", limit: "20" });
      if (pageParam) params.set("cursor", pageParam.previous.next_cursor!);
      const page = decodeReportPage(await api<unknown>(
        `/projects/${encodeURIComponent(projectId)}/job-completion-reports?${params}`, { signal }
      ), projectId, { previous: pageParam?.previous });
      if (pageParam) {
        const seen = new Set(pageParam.pages.flatMap((entry) => entry.items.map((item) => item.report.id.toLowerCase())));
        if (page.items.some((item) => seen.has(item.report.id.toLowerCase()))
          || page.next_cursor !== null && pageParam.pages.some((entry) => entry.next_cursor === page.next_cursor)) {
          throw new Error("Mnemonic returned incoherent report pagination.");
        }
      }
      return page;
    },
    getNextPageParam: (page, pages) => page.next_cursor === null ? undefined : { previous: page, pages }
  });
}
