import { readBoundedJson } from "./bounded-json.ts";
import { boundedText, exactKeys, finiteInteger, objectValue, sameUuid, validUuid } from "./wire-guards.ts";

export const ARTIFACT_DEFAULT_MAX_BYTES = 64 * 1024 * 1024;
export const ARTIFACT_STATUS_PATH = "/api/artifacts/status";
export const ARTIFACT_DISABLED_MESSAGE = "The artifact library is disabled. Stored files and metadata are preserved.";
export const ARTIFACT_SORTS = ["filename", "created_at", "modified_at", "size_bytes", "revision"] as const;
export type ArtifactSort = typeof ARTIFACT_SORTS[number];

export interface ArtifactStatus {
  enabled: boolean;
  max_bytes: number;
  message: string;
}

export function decodeArtifactStatus(value: unknown): ArtifactStatus {
  const status = objectValue(value);
  if (!status || !exactKeys(status, ["enabled", "max_bytes", "message"])
    || typeof status.enabled !== "boolean" || !finiteInteger(status.max_bytes, 0, 1024 * 1024 * 1024)
    || status.enabled !== (status.max_bytes > 0) || !boundedText(status.message, 1000)) {
    throw new Error("Mnemonic returned an invalid artifact status.");
  }
  return { enabled: status.enabled, max_bytes: status.max_bytes, message: status.message };
}

export async function fetchArtifactStatus(signal?: AbortSignal, fetcher: typeof fetch = fetch): Promise<ArtifactStatus> {
  const response = await fetcher(ARTIFACT_STATUS_PATH, { cache: "no-store", signal });
  if (response.status !== 200) throw new Error("Artifact status is unavailable. Refresh to check whether the library is enabled.");
  return decodeArtifactStatus(await readBoundedJson(response, 16 * 1024));
}

export function decodeArtifactLimitError(value: unknown, status: number): { code: "artifact_library_disabled" | "artifact_too_large"; message: string; maxBytes: number } | null {
  const root = objectValue(value);
  const detail = objectValue(root?.detail);
  const context = objectValue(detail?.context);
  if (!root || !exactKeys(root, ["detail"]) || !detail || !exactKeys(detail, ["code", "message", "context"])
    || !context || !exactKeys(context, ["max_bytes"]) || !boundedText(detail.message, 1000)
    || !finiteInteger(context.max_bytes, 0, 1024 * 1024 * 1024)) return null;
  if (status === 503 && detail.code === "artifact_library_disabled" && context.max_bytes === 0
    || status === 413 && detail.code === "artifact_too_large" && context.max_bytes > 0) {
    return { code: detail.code, message: detail.message, maxBytes: context.max_bytes };
  }
  return null;
}

export interface Artifact {
  id: string;
  project_id: string;
  filename: string;
  description: string | null;
  revision: number;
  size_bytes: number;
  sha256: string;
  mime_type: string | null;
  created_at: string;
  modified_at: string;
  deleted_at: string | null;
  content_available: boolean;
  created_by_agent_session_id: string | null;
  originating_work_item_id: string | null;
  related_work_item_ids: string[];
}

export interface ArtifactPage {
  items: Artifact[];
  total: number;
  limit: number;
  offset: number;
}

function timestamp(value: unknown): value is string {
  return typeof value === "string" && value.length <= 40 && Number.isFinite(Date.parse(value));
}

export function decodeArtifact(value: unknown, projectId: string): Artifact {
  const row = objectValue(value);
  if (!row || !validUuid(row.id) || !sameUuid(row.project_id, projectId)
    || !boundedText(row.filename, 255) || !finiteInteger(row.revision, 1)
    || !finiteInteger(row.size_bytes) || typeof row.sha256 !== "string"
    || !/^[a-f0-9]{64}$/.test(row.sha256)
    || !(row.description === null || typeof row.description === "string" && row.description.length <= 4000)
    || !(row.mime_type === null || boundedText(row.mime_type, 200))
    || !timestamp(row.created_at) || !timestamp(row.modified_at)
    || !(row.deleted_at === null || timestamp(row.deleted_at))
    || typeof row.content_available !== "boolean"
    || !(row.created_by_agent_session_id === null || boundedText(row.created_by_agent_session_id, 200))
    || !(row.originating_work_item_id === null || validUuid(row.originating_work_item_id))
    || !Array.isArray(row.related_work_item_ids) || row.related_work_item_ids.length > 50
    || !row.related_work_item_ids.every(validUuid)) {
    throw new Error("Mnemonic returned invalid artifact metadata. Refresh and try again.");
  }
  if (new Set(row.related_work_item_ids.map((id) => id.toLowerCase())).size !== row.related_work_item_ids.length) throw new Error("Mnemonic returned duplicate artifact work links.");
  return row as unknown as Artifact;
}

export function decodeArtifactPage(value: unknown, projectId: string): ArtifactPage {
  const page = objectValue(value);
  if (!page || !Array.isArray(page.items) || page.items.length > 100
    || !finiteInteger(page.total) || !finiteInteger(page.limit, 1, 100)
    || !finiteInteger(page.offset) || page.items.length > page.limit) {
    throw new Error("Mnemonic returned an invalid artifact listing.");
  }
  return { items: page.items.map((row) => decodeArtifact(row, projectId)), total: page.total, limit: page.limit, offset: page.offset };
}

export function artifactPath(projectId: string, artifactId?: string): string {
  if (!validUuid(projectId) || artifactId !== undefined && !validUuid(artifactId)) throw new Error("Invalid artifact identity.");
  return `/api/artifacts/projects/${projectId}/artifacts${artifactId ? `/${artifactId}` : ""}`;
}

export function artifactLibraryPath(projectId: string, workItemId?: string): string {
  if (!validUuid(projectId) || workItemId !== undefined && !validUuid(workItemId)) throw new Error("Invalid artifact workspace identity.");
  const query = new URLSearchParams({ project: projectId });
  if (workItemId) query.set("work", workItemId);
  return `/artifacts?${query}`;
}

export function artifactLocation(search: string): { projectId: string | null; workItemId: string | null } {
  const query = new URLSearchParams(search);
  const projectId = query.get("project");
  const workItemId = query.get("work");
  if (query.getAll("project").length !== 1 || !validUuid(projectId)) return { projectId: null, workItemId: null };
  return { projectId, workItemId: query.getAll("work").length === 1 && validUuid(workItemId) ? workItemId : null };
}

export function formatArtifactSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
