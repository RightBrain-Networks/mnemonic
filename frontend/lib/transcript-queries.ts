import { queryOptions, type QueryClient } from "@tanstack/react-query";
import { queryKeys } from "./query-keys.ts";
import { transcriptSearchRead } from "./search-read.ts";
import { decodeTranscriptHealth } from "./transcript-health.ts";
import {
  decodeTranscriptSegments, transcriptSegmentQuery,
  type TranscriptContentKind, type TranscriptSegmentLocator
} from "./transcript-segments.ts";
import {
  decodeTranscript, decodeTranscriptPage, decodeTranscriptSettings, decodeTranscriptText,
  transcriptPath, transcriptRequest, transcriptSettingsPath,
  TRANSCRIPT_PAGE_SIZE, TRANSCRIPT_TEXT_PAGE_SIZE, type Transcript, type TranscriptSort
} from "./transcripts.ts";

const root = (projectId: string) => [...queryKeys.project(projectId), "transcripts"] as const;
export const transcriptKeys = {
  root,
  list: (projectId: string, filters: TranscriptFilters) => [...root(projectId), "list", filters] as const,
  detail: (projectId: string, transcriptId: string) => [...root(projectId), "detail", transcriptId] as const,
  text: (projectId: string, transcriptId: string, digest: string, offset: number) =>
    [...root(projectId), "text", transcriptId, { digest, offset, limit: TRANSCRIPT_TEXT_PAGE_SIZE }] as const,
  segments: (projectId: string, transcriptId: string, digest: string | null, locator: TranscriptSegmentLocator) =>
    [...root(projectId), "segments", transcriptId, { digest, ...locator, limit: TRANSCRIPT_TEXT_PAGE_SIZE }] as const,
  health: (projectId: string) => [...root(projectId), "health"] as const,
  settings: (projectId: string) => [...root(projectId), "settings"] as const
};

export type TranscriptFilters = {
  search: string; fulltext: boolean; offset: number; workItemId: string;
  contentKind: TranscriptContentKind | ""; sortBy: TranscriptSort | null; sortDirection: "asc" | "desc";
};

export function transcriptIsProcessing(transcript: Transcript): boolean {
  return [transcript.status, transcript.copy_status, transcript.index_status, transcript.normalization_status]
    .some((status) => ["waiting", "pending", "processing"].includes(status));
}

export function transcriptEvidenceChanged(pinned: Transcript, current: Transcript | undefined): boolean {
  return current !== undefined && (current.id !== pinned.id || current.project_id !== pinned.project_id
    || current.text_sha256 !== pinned.text_sha256 || current.normalized_revision !== pinned.normalized_revision
    || current.status !== pinned.status || current.normalization_status !== pinned.normalization_status);
}

export function transcriptListOptions(projectId: string, filters: TranscriptFilters) {
  // Disabled content filters do not change the request or its cache identity.
  const request: TranscriptFilters = { ...filters, contentKind: filters.fulltext ? filters.contentKind : "" };
  return queryOptions({
    queryKey: transcriptKeys.list(projectId, request),
    queryFn: async ({ signal }) => {
      const contentKinds = request.contentKind ? [request.contentKind] : undefined;
      const params = new URLSearchParams({ fulltext: String(request.fulltext), detail: "full",
        offset: String(request.offset), limit: String(TRANSCRIPT_PAGE_SIZE), sort_direction: request.sortDirection });
      if (request.search) params.set("query", request.search);
      if (request.workItemId) params.set("work_item_id", request.workItemId);
      if (contentKinds) params.set("content_kinds", contentKinds[0]);
      if (request.sortBy) params.set("sort_by", request.sortBy);
      const value = await transcriptSearchRead(() => transcriptRequest(`${transcriptPath(projectId)}?${params}`, { signal }), signal);
      return decodeTranscriptPage(value, projectId, request.offset, request.fulltext, request.workItemId || undefined, contentKinds);
    },
    retry: false,
    // Captures, imports and worker transitions do not publish live invalidations.
    // This discovery read must continue even when the current page is empty.
    refetchInterval: 30_000,
    refetchIntervalInBackground: false
  });
}

export function transcriptDetailOptions(projectId: string, transcriptId: string) {
  return queryOptions({
    queryKey: transcriptKeys.detail(projectId, transcriptId),
    queryFn: async ({ signal }) => decodeTranscript(await transcriptRequest(transcriptPath(projectId, transcriptId), { signal }), projectId, transcriptId),
    refetchInterval: (query) => query.state.data && transcriptIsProcessing(query.state.data) ? 30_000 : false,
    refetchIntervalInBackground: false
  });
}

export function transcriptTextOptions(projectId: string, transcriptId: string, digest: string, offset: number) {
  return queryOptions({
    queryKey: transcriptKeys.text(projectId, transcriptId, digest, offset),
    queryFn: async ({ signal }) => {
      const params = new URLSearchParams({ offset: String(offset), limit: String(TRANSCRIPT_TEXT_PAGE_SIZE), expected_sha256: digest });
      return decodeTranscriptText(await transcriptRequest(`${transcriptPath(projectId, transcriptId)}/text?${params}`, { signal }), transcriptId, digest, offset, projectId);
    }
  });
}

export function transcriptSegmentOptions(projectId: string, transcriptId: string, digest: string | null, locator: TranscriptSegmentLocator) {
  return queryOptions({
    queryKey: transcriptKeys.segments(projectId, transcriptId, digest, locator),
    queryFn: async ({ signal }) => {
      const params = transcriptSegmentQuery(locator, digest);
      return decodeTranscriptSegments(await transcriptRequest(`${transcriptPath(projectId, transcriptId)}/text?${params}`, { signal }), projectId, transcriptId, locator, digest);
    }
  });
}

export function transcriptHealthOptions(projectId: string) {
  return queryOptions({
    queryKey: transcriptKeys.health(projectId),
    queryFn: async ({ signal }) => decodeTranscriptHealth(await transcriptRequest(`${transcriptPath(projectId)}/health`, { signal }), projectId),
    refetchInterval: 30_000,
    refetchIntervalInBackground: false
  });
}

export function transcriptSettingsOptions(projectId: string) {
  return queryOptions({
    queryKey: transcriptKeys.settings(projectId),
    queryFn: async ({ signal }) => decodeTranscriptSettings(await transcriptRequest(transcriptSettingsPath(projectId), { signal }))
  });
}

export function invalidateTranscriptReads(client: QueryClient, projectId: string | null) {
  return client.invalidateQueries(projectId === null ? {
    predicate: ({ queryKey }) => queryKey[0] === "mnemonic" && queryKey[1] === "project" && queryKey[3] === "transcripts"
  } : { queryKey: transcriptKeys.root(projectId) });
}
