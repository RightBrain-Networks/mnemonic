import { infiniteQueryOptions, queryOptions } from "@tanstack/react-query";
import { api, completionEvidenceApi, workItemPath } from "./api.ts";
import {
  decodeCodeReviewDetail, decodeReviewQueuePage, decodeWorkFollowUpDetail,
  type ReviewQueuePage
} from "./code-reviews.ts";
import { mergeCompletionEvidencePage } from "./completion-evidence.ts";
import { decodeHumanGatePage, humanGateHistorySearchParams, humanGatePath } from "./human-gates.ts";
import { workDetailKeys } from "./work-detail-queries.ts";
import { decodeWorkEventPage, EVENT_PAGE_SIZE, workEventSearchParams } from "./work-events.ts";
import type { CompletionEvidencePage, WorkEventType } from "./types.ts";

const detail = workDetailKeys.work;
export const workPanelKeys = {
  detail,
  events: (projectId: string, workId: string, eventType: WorkEventType | "", offset: number) =>
    [...detail(projectId, workId), "events", { eventType, offset, limit: EVENT_PAGE_SIZE }] as const,
  evidence: (projectId: string, workId: string) => [...detail(projectId, workId), "evidence", { limit: 10 }] as const,
  gates: (projectId: string, workId: string, cursor: string | null) =>
    [...detail(projectId, workId), "gates", { status: "all", limit: 30, cursor }] as const,
  reviewHistory: (projectId: string, workId: string, kind: "reviews" | "questions") =>
    [...detail(projectId, workId), "review-history", { kind, state: "all", limit: 20 }] as const,
  review: (projectId: string, workId: string, reviewId: string) =>
    [...detail(projectId, workId), "review", reviewId] as const,
  followUp: (projectId: string, workId: string, followUpId: string) =>
    [...detail(projectId, workId), "follow-up", followUpId] as const
};

export function workEventsOptions(projectId: string, workId: string, eventType: WorkEventType | "", offset: number) {
  return queryOptions({
    queryKey: workPanelKeys.events(projectId, workId, eventType, offset),
    queryFn: async ({ signal }) => decodeWorkEventPage(await api<unknown>(
      `${workItemPath(projectId, workId)}/events?${workEventSearchParams({ eventType, limit: EVENT_PAGE_SIZE, offset })}`,
      { signal }
    ), projectId, workId)
  });
}

type EvidenceCursor = { cursor: string | null; pages: CompletionEvidencePage[] };
type EvidenceReader = (cursor: string | null, signal: AbortSignal) => Promise<CompletionEvidencePage>;

export function completionEvidenceOptions(projectId: string, workId: string, readPage?: EvidenceReader) {
  return infiniteQueryOptions({
    queryKey: workPanelKeys.evidence(projectId, workId),
    initialPageParam: { cursor: null, pages: [] } as EvidenceCursor,
    queryFn: async ({ pageParam, signal }) => {
      const params = new URLSearchParams({ limit: "10" });
      if (pageParam.cursor) params.set("cursor", pageParam.cursor);
      const next = await (readPage ? readPage(pageParam.cursor, signal) : completionEvidenceApi(
        `${workItemPath(projectId, workId)}/completion-evidence?${params}`, workId, { signal }
      ));
      if (pageParam.pages.length) {
        // Validate the complete cursor chain before publishing any appended page.
        // Infinite refetches build these parameters from their freshly read head.
        mergeCompletionEvidencePage(pageParam.pages[0], pageParam.pages.flatMap((page) => page.items), next);
      }
      return next;
    },
    getNextPageParam: (last, pages): EvidenceCursor | undefined => last.next_cursor === null
      ? undefined : { cursor: last.next_cursor, pages }
  });
}

export function humanGateHistoryOptions(projectId: string, workId: string, cursor: string | null, enabled = true) {
  return queryOptions({
    // Closing history must detach its observer, not only prevent new fetches:
    // enabled:false on the existing key leaves an in-flight request running.
    queryKey: enabled ? workPanelKeys.gates(projectId, workId, cursor)
      : ["mnemonic", "disabled", "work-gate-history"] as const,
    enabled,
    queryFn: async ({ signal }) => decodeHumanGatePage(await api<unknown>(
      `${humanGatePath(projectId, workId)}?${humanGateHistorySearchParams({ status: "all", limit: 30, cursor })}`,
      { signal }
    ), projectId, workId, { status: "all", limit: 30 })
  });
}

export function reviewHistoryOptions(projectId: string, workId: string, kind: "reviews" | "questions") {
  return infiniteQueryOptions({
    queryKey: workPanelKeys.reviewHistory(projectId, workId, kind),
    initialPageParam: null as string | null,
    queryFn: async ({ pageParam, signal }) => {
      const params = new URLSearchParams({ state: "all", work_item_id: workId, limit: "20" });
      if (pageParam) params.set("after", pageParam);
      const route = kind === "reviews" ? "code-reviews" : "work-agent-follow-ups";
      return decodeReviewQueuePage(await api<unknown>(`/projects/${projectId}/${route}?${params}`, { signal }),
        projectId, kind === "reviews" ? "reviews" : "follow-ups");
    },
    getNextPageParam: (last) => last.has_more ? last.next_cursor : undefined
  });
}

export function mergeReviewHistory(pages: readonly ReviewQueuePage[]): ReviewQueuePage | null {
  if (!pages.length) return null;
  const rows = new Map<string, ReviewQueuePage["items"][number]>();
  for (const page of pages) for (const row of page.items) if (!rows.has(row.id)) rows.set(row.id, row);
  return { ...pages[pages.length - 1], items: [...rows.values()] };
}

export function codeReviewDetailOptions(projectId: string, workId: string, reviewId: string) {
  return queryOptions({
    queryKey: workPanelKeys.review(projectId, workId, reviewId),
    queryFn: async ({ signal }) => decodeCodeReviewDetail(await api<unknown>(
      `${workItemPath(projectId, workId)}/code-reviews/${reviewId}`, { signal }
    ), projectId, workId, reviewId)
  });
}

export function workFollowUpOptions(projectId: string, workId: string, followUpId: string) {
  return queryOptions({
    queryKey: workPanelKeys.followUp(projectId, workId, followUpId),
    queryFn: async ({ signal }) => decodeWorkFollowUpDetail(await api<unknown>(
      `${workItemPath(projectId, workId)}/agent-follow-ups/${followUpId}`, { signal }
    ), projectId, workId, followUpId)
  });
}
