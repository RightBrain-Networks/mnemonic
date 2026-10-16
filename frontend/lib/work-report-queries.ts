import { infiniteQueryOptions, queryOptions, type QueryClient } from "@tanstack/react-query";
import { api, workItemPath } from "./api.ts";
import { decodeReportDetail, decodeReportProvenancePage } from "./job-completion-reports.ts";
import { workDetailKeys } from "./work-detail-queries.ts";
import { compareUtcDateTimes } from "./wire-guards.ts";
import type { JobReportProvenancePage } from "./types.ts";

type Direction = "origin" | "created";
type ReportSelection = { projectId: string; id: string };
type ProvenanceCursor = { cursor: string | null; pages: JobReportProvenancePage[] };

export const workReportKeys = {
  provenance: (projectId: string, workId: string, direction: Direction) =>
    [...workDetailKeys.work(projectId, workId), "report-follow-ups", { direction, limit: 20 }] as const,
  report: (projectId: string, workId: string, reportProjectId: string, reportId: string) =>
    [...workDetailKeys.work(projectId, workId), "report", reportProjectId, reportId] as const
};

function validateProvenanceAppend(pages: JobReportProvenancePage[], next: JobReportProvenancePage) {
  if (!pages.length) return;
  const loaded = pages.flatMap((page) => page.items);
  const previous = loaded.at(-1);
  const identities = new Set(loaded.map((item) => item.id.toLowerCase()));
  if (next.as_of_sequence !== pages[0]!.as_of_sequence
    || next.next_cursor !== null && pages.some((page) => page.next_cursor === next.next_cursor)
    || next.items.some((item) => {
      if (identities.has(item.id.toLowerCase())) return true;
      if (!previous) return false;
      const order = compareUtcDateTimes(item.created_at, previous.created_at);
      return order < 0 || order === 0 && item.id.toLowerCase() <= previous.id.toLowerCase();
    })) throw new Error("Mnemonic returned incoherent report provenance.");
}

export function workReportProvenanceOptions(projectId: string, workId: string, direction: Direction) {
  return infiniteQueryOptions({
    queryKey: workReportKeys.provenance(projectId, workId, direction),
    initialPageParam: { cursor: null, pages: [] } as ProvenanceCursor,
    queryFn: async ({ pageParam, signal }) => {
      const params = new URLSearchParams({ direction, limit: "20" });
      if (pageParam.cursor) params.set("cursor", pageParam.cursor);
      const page = decodeReportProvenancePage(await api<unknown>(
        `${workItemPath(projectId, workId)}/report-follow-ups?${params}`, { signal }
      ), projectId, { workItemId: workId, direction });
      validateProvenanceAppend(pageParam.pages, page);
      return page;
    },
    getNextPageParam: (last, pages): ProvenanceCursor | undefined => last.next_cursor === null
      ? undefined : { cursor: last.next_cursor, pages }
  });
}

/** Report authority stays at its original project even after the viewed work moves. */
export function workReportDetailOptions(projectId: string, workId: string, selected: ReportSelection | null) {
  return queryOptions({
    queryKey: selected ? workReportKeys.report(projectId, workId, selected.projectId, selected.id)
      : ["mnemonic", "disabled", "work-report-detail"] as const,
    enabled: selected !== null,
    staleTime: 0,
    queryFn: async ({ signal }) => {
      if (!selected) throw new Error("Select an originating report before reading its detail.");
      return decodeReportDetail(await api<unknown>(
        `/projects/${encodeURIComponent(selected.projectId)}/job-completion-reports/${encodeURIComponent(selected.id)}`,
        { signal }
      ), selected.projectId, selected.id);
    }
  });
}

/** Report notifications also cover reports viewed through another project's work. */
export function invalidateWorkReportReads(client: QueryClient, projectId: string | null) {
  return client.invalidateQueries({
    predicate: ({ queryKey }) => queryKey[0] === "mnemonic" && queryKey[1] === "project"
      && queryKey[3] === "work-detail"
      && (queryKey[5] === "report" || queryKey[5] === "report-follow-ups")
      && (projectId === null || queryKey[2] === projectId
        || queryKey[5] === "report" && queryKey[6] === projectId),
    refetchType: "active"
  }, { cancelRefetch: false });
}
