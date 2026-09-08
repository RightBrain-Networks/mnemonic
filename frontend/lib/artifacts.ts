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
  extraction: ArtifactExtraction;
}

export interface ArtifactExtraction {
  status: "pending" | "processing" | "ready" | "failed" | "superseded" | "deleted";
  metadata: Record<string, string[]>;
  truncated: boolean;
  error_code: string | null;
  extracted_at: string | null;
}

function decodeArtifactExtraction(value: unknown): ArtifactExtraction {
  if (value === undefined) return { status: "pending", metadata: {}, truncated: false, error_code: null, extracted_at: null };
  const extraction = objectValue(value);
  const metadata = objectValue(extraction?.metadata);
  if (!extraction || !exactKeys(extraction, ["status", "metadata", "truncated", "error_code", "extracted_at"])
    || !["pending", "processing", "ready", "failed", "superseded", "deleted"].includes(extraction.status as string)
    || typeof extraction.truncated !== "boolean" || !metadata || Object.keys(metadata).length > 64
    || !Object.entries(metadata).every(([key, values]) => key.length > 0 && Array.from(key).length <= 128
      && Array.isArray(values) && values.length <= 8 && values.every((value) => typeof value === "string" && Array.from(value).length <= 512))
    || !(extraction.error_code === null || boundedText(extraction.error_code, 100))
    || !(extraction.extracted_at === null || timestamp(extraction.extracted_at))) {
    throw new Error("Mnemonic returned invalid extracted artifact metadata.");
  }
  // Tika metadata uses Python's ensure_ascii JSON budget, including separator spaces.
  const encoded = `{${Object.entries(metadata).map(([key, values]) => `${JSON.stringify(key)}: [${(values as string[]).map((value) => JSON.stringify(value)).join(", ")}]`).join(", ")}}`;
  if (encoded.replace(/[\x7f-\uffff]/g, (character) => `\\u${character.charCodeAt(0).toString(16).padStart(4, "0")}`).length > 8192) throw new Error("Mnemonic returned oversized extracted artifact metadata.");
  return extraction as unknown as ArtifactExtraction;
}

export interface ArtifactPage {
  items: Artifact[];
  total: number;
  limit: number;
  offset: number;
}

export interface ArtifactIndexingStatus {
  pending: number;
  failed: number;
  ready: number;
  truncated: number;
}

export interface ArtifactSearchMatch {
  artifact: Artifact;
  score: number;
  snippet: string | null;
  matched_fields: ("metadata" | "content")[];
}

export interface ArtifactSearchPage {
  items: ArtifactSearchMatch[];
  total: number;
  limit: number;
  offset: number;
  fulltext: boolean;
  indexing: ArtifactIndexingStatus;
}

export function decodeArtifactSearchPage(value: unknown, projectId: string, fulltext: boolean, limit = 50, offset = 0): ArtifactSearchPage {
  const page = objectValue(value);
  const indexing = objectValue(page?.indexing);
  if (!page || !exactKeys(page, ["items", "total", "limit", "offset", "fulltext", "indexing"])
    || !Array.isArray(page.items) || !finiteInteger(page.total) || page.limit !== limit || page.offset !== offset
    || page.items.length !== Math.min(limit, Math.max(0, page.total - offset)) || page.fulltext !== fulltext
    || !indexing || !exactKeys(indexing, ["pending", "failed", "ready", "truncated"])
    || !Object.values(indexing).every((count) => finiteInteger(count))) {
    throw new Error("Mnemonic returned invalid artifact search results.");
  }
  const items = page.items.map((value): ArtifactSearchMatch => {
    const match = objectValue(value);
    if (!match || !exactKeys(match, ["artifact", "score", "snippet", "matched_fields"])
      || typeof match.score !== "number" || !Number.isFinite(match.score) || match.score < 0
      || !(match.snippet === null || typeof match.snippet === "string" && match.snippet.length <= 1000)
      || !Array.isArray(match.matched_fields) || !match.matched_fields.length || match.matched_fields.length > 2
      || !match.matched_fields.every((field) => field === "metadata" || field === "content")
      || new Set(match.matched_fields).size !== match.matched_fields.length) {
      throw new Error("Mnemonic returned an invalid artifact search match.");
    }
    const artifact = decodeArtifact(match.artifact, projectId);
    if ((!fulltext || artifact.deleted_at !== null) && (match.snippet !== null || match.matched_fields.includes("content"))) {
      throw new Error("Mnemonic returned content outside the requested search scope.");
    }
    return { artifact, score: match.score, snippet: match.snippet, matched_fields: match.matched_fields as ("metadata" | "content")[] };
  });
  if (new Set(items.map((item) => item.artifact.id.toLowerCase())).size !== items.length) throw new Error("Mnemonic returned duplicate artifact search matches.");
  return { items, total: page.total, limit, offset, fulltext, indexing: indexing as unknown as ArtifactIndexingStatus };
}

export function validArtifactSearchRequest(value: unknown): boolean {
  const request = objectValue(value);
  return Boolean(request && Object.keys(request).every((key) => ["q", "fulltext", "work_item_id", "artifact_id", "include_deleted", "limit", "offset"].includes(key))
    && boundedText(request.q, 200) && (request.q as string).trim().length > 0
    && (request.fulltext === undefined || typeof request.fulltext === "boolean")
    && (request.include_deleted === undefined || typeof request.include_deleted === "boolean")
    && (request.work_item_id === undefined || validUuid(request.work_item_id))
    && (request.artifact_id === undefined || validUuid(request.artifact_id))
    && (request.limit === undefined || finiteInteger(request.limit, 1, 100))
    && (request.offset === undefined || finiteInteger(request.offset, 0, 1_000_000)));
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
  return { ...row, extraction: decodeArtifactExtraction(row.extraction) } as unknown as Artifact;
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
