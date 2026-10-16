import { queryOptions, type QueryClient } from "@tanstack/react-query";
import { detailMessage } from "./api.ts";
import { artifactPath, decodeArtifact, decodeArtifactLimitError, decodeArtifactPage, fetchArtifactStatus, type Artifact, type ArtifactPage, type ArtifactSearchMatch, type ArtifactSearchPage, type ArtifactSort } from "./artifacts.ts";
import { artifactPassageQuery, decodeArtifactPassageText, verifyArtifactPassageIds } from "./artifact-semantic.ts";
import { readBoundedJson } from "./bounded-json.ts";
import { queryKeys } from "./query-keys.ts";
import { artifactSearchRequest, decodeUnifiedArtifactSearchPage, unifiedSearchPath } from "./unified-search.ts";

const root = (projectId: string) => [...queryKeys.project(projectId), "artifacts"] as const;
export const artifactKeys = {
  status: ["mnemonic", "artifact-status"] as const,
  root,
  page: (filters: ArtifactFilters) => [...root(filters.projectId), "page", filters] as const,
  detail: (projectId: string, id: string) => [...root(projectId), "detail", id] as const,
  passage: (match: ArtifactSearchMatch) => [...root(match.artifact.project_id), "passage", match.artifact.id,
    { revision: match.artifact.revision, sha256: match.artifact.sha256, sensitive: match.artifact.sensitive,
      passage: match.passage, snippet: match.snippet, truncated: match.artifact.extraction.truncated }] as const
};

export type ArtifactFilters = {
  projectId: string; search: string; fulltext: boolean; semantic: boolean; includeDeleted: boolean;
  workItemId: string; sort: ArtifactSort; order: "asc" | "desc"; offset: number; limit: number;
};
export type ArtifactReadPage = { page: ArtifactPage | null; searchPage: ArtifactSearchPage | null };

export function artifactStatusOptions() {
  return queryOptions({
    queryKey: artifactKeys.status,
    queryFn: ({ signal }) => fetchArtifactStatus(signal),
    staleTime: 0,
    retry: false,
    refetchInterval: 30_000,
    refetchIntervalInBackground: false
  });
}

async function requireArtifactStatus(client: QueryClient, signal: AbortSignal) {
  // The status observer and directory poll can tick together. Share a status
  // checked within this short window instead of dispatching a second request.
  const status = await client.fetchQuery({ ...artifactStatusOptions(), staleTime: 1_000 });
  signal.throwIfAborted();
  return status.enabled;
}

async function artifactJson(client: QueryClient, response: Response, signal: AbortSignal) {
  const value = await readBoundedJson(response, 4 * 1024 * 1024);
  signal.throwIfAborted();
  const limit = decodeArtifactLimitError(value, response.status);
  if (limit?.code === "artifact_library_disabled") {
    await client.cancelQueries({ queryKey: artifactKeys.status, exact: true });
    client.setQueryData(artifactKeys.status, { enabled: false, max_bytes: 0, message: limit.message });
  }
  if (!response.ok) throw new Error(detailMessage((value as { detail?: unknown }).detail).message || "Unable to load artifacts.");
  return value;
}

export function artifactPageOptions(client: QueryClient, filters: ArtifactFilters, enabled = true) {
  const { projectId, search, fulltext, semantic, includeDeleted, workItemId, sort, order, offset, limit } = filters;
  return queryOptions({
    queryKey: enabled ? artifactKeys.page(filters) : ["mnemonic", "disabled", "artifact-page", filters] as const,
    // Query owns semantic results, but only explicit user actions may dispatch
    // inference. Enabling an observer after status recovery must not retry it.
    enabled: enabled && !(semantic && search),
    placeholderData: (previous: ArtifactReadPage | undefined, previousQuery) => {
      // Preserve the directory's scroll range for sorting/paging only. A different
      // project, search mode or filter must never display the old scope's rows.
      const old = previousQuery?.queryKey[5] as ArtifactFilters | undefined;
      return enabled && old && ["projectId", "search", "fulltext", "semantic", "includeDeleted", "workItemId", "limit"]
        .every((key) => old[key as keyof ArtifactFilters] === filters[key as keyof ArtifactFilters]) ? previous : undefined;
    },
    queryFn: async ({ signal }): Promise<ArtifactReadPage> => {
      if (!await requireArtifactStatus(client, signal)) return { page: null, searchPage: null };
      const params = new URLSearchParams({ sort, order, limit: String(limit), offset: String(offset), include_deleted: String(includeDeleted) });
      if (workItemId) params.set("work_item_id", workItemId);
      const response = search ? await fetch(`/api/mnemonic${unifiedSearchPath(projectId)}`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(artifactSearchRequest(search, fulltext, includeDeleted, limit, offset, workItemId || undefined, semantic)),
        cache: "no-store", signal
      }) : await fetch(`${artifactPath(projectId)}?${params}`, { cache: "no-store", signal });
      const value = await artifactJson(client, response, signal);
      if (!search) return { page: decodeArtifactPage(value, projectId), searchPage: null };
      const matches = decodeUnifiedArtifactSearchPage(value, projectId, fulltext, limit, offset, includeDeleted, workItemId || undefined, search, "terms", semantic);
      await verifyArtifactPassageIds(matches.items);
      signal.throwIfAborted();
      if (!includeDeleted && matches.items.some((item) => item.artifact.deleted_at !== null)) throw new Error("Mnemonic returned deleted artifacts outside the requested search scope.");
      return { page: { ...matches, items: matches.items.map((item) => item.artifact) }, searchPage: matches };
    },
    // Semantic inference is repeated only by an explicit search or retry.
    staleTime: semantic && Boolean(search) ? Infinity : 0,
    retry: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    refetchOnMount: !(semantic && search),
    refetchInterval: semantic && search ? false : 30_000,
    refetchIntervalInBackground: false,
    meta: { artifactSemantic: Boolean(semantic && search) }
  });
}

export function prepareArtifactPageNavigation(client: QueryClient, filters: ArtifactFilters) {
  if (filters.semantic && filters.search) return;
  // Returning to an inactive cached sort/page must keep the current displayed
  // range until the new read completes, just like navigation to an uncached page.
  client.removeQueries({ queryKey: artifactKeys.page(filters), exact: true, type: "inactive" });
}

export function artifactDetailOptions(client: QueryClient, projectId: string, id: string, enabled = true) {
  return queryOptions({
    queryKey: enabled ? artifactKeys.detail(projectId, id) : ["mnemonic", "disabled", "artifact-detail", projectId] as const,
    enabled,
    queryFn: async ({ signal }) => {
      if (!await requireArtifactStatus(client, signal)) throw new Error("The artifact library is disabled.");
      const value = await artifactJson(client, await fetch(artifactPath(projectId, id), { cache: "no-store", signal }), signal);
      return decodeArtifact(value, projectId, id);
    },
    staleTime: 0,
    refetchInterval: 30_000,
    refetchIntervalInBackground: false
  });
}

export function artifactPreviewCurrent(expected: Artifact, current: Artifact | undefined) {
  return Boolean(current && current.project_id === expected.project_id && current.id === expected.id
    && current.revision === expected.revision && current.sha256 === expected.sha256
    && current.sensitive === expected.sensitive && current.content_available && !current.deleted_at);
}

export function artifactPassageOptions(match: ArtifactSearchMatch | undefined, enabled = true) {
  return queryOptions({
    queryKey: enabled && match?.passage ? artifactKeys.passage(match) : ["mnemonic", "disabled", "artifact-passage"] as const,
    enabled: enabled && Boolean(match?.passage),
    queryFn: async ({ signal }) => {
      if (!match?.passage) throw new Error("Select a verified artifact passage.");
      const response = await fetch(`${artifactPath(match.artifact.project_id, match.artifact.id)}/text?${artifactPassageQuery(match.passage)}`, { cache: "no-store", signal });
      if (response.status === 409) throw new Error("This passage changed after the search. Close the preview and search again.");
      if (!response.ok) throw new Error("Unable to read this verified passage. Close the preview and search again.");
      return decodeArtifactPassageText(await readBoundedJson(response, 256 * 1024), match);
    },
    // Content is retained only while its drawer is mounted, including sensitive text.
    gcTime: 0,
    staleTime: 0,
    retry: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false
  });
}

export function invalidateArtifactReads(client: QueryClient, projectId: string | null, options: { semantic?: boolean } = {}) {
  const matches = (key: readonly unknown[]) => key[0] === "mnemonic" && key[1] === "project"
    && key[3] === "artifacts" && (projectId === null || key[2] === projectId);
  return Promise.all([
    client.invalidateQueries({ queryKey: artifactKeys.status, exact: true }, { cancelRefetch: false }),
    client.invalidateQueries({ predicate: (query) => matches(query.queryKey) && !query.meta?.artifactSemantic && query.queryKey[4] !== "passage" }, { cancelRefetch: false }),
    options.semantic === false ? Promise.resolve() : client.invalidateQueries({ predicate: (query) => matches(query.queryKey) && Boolean(query.meta?.artifactSemantic), refetchType: "none" })
  ]);
}

/** Call only after the artifact executor has decoded a confirmed success. */
export async function reconcileArtifactMutation(client: QueryClient, artifact: Artifact) {
  // A confirmed write fences all pre-write page/detail/evidence responses.
  // Live invalidation deliberately preserves in-flight reads; writes cannot.
  await client.cancelQueries({ queryKey: root(artifact.project_id) });
  client.setQueryData(artifactKeys.detail(artifact.project_id, artifact.id), artifact);
  const passage = [...root(artifact.project_id), "passage", artifact.id];
  await client.cancelQueries({ queryKey: passage });
  client.removeQueries({ queryKey: passage });
  await invalidateArtifactReads(client, artifact.project_id);
}
