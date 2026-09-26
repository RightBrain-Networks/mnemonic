import { validSearchPagination, type SearchPagination } from "./search-pagination.ts";
import { transcriptCoverageLabel } from "./transcript-coverage.ts";
import type { QueryMode } from "./search-evidence.ts";
import { validateSourceRanking, decodeSearchRanking, decodeHitRanking, validScoreType, type SearchRanking, type ScoreType } from "./search-ranking.ts";
import { validTranscriptNormalization, type TranscriptNormalization, type TranscriptContentKind } from "./transcript-segments.ts";
import { decodeSearchDisclosure, validateSearchDisclosure, type SearchDisclosure } from "./search-disclosure.ts";
import { readBoundedJson } from "./bounded-json.ts";
import { decodeTermDiagnostics, type TermDiagnostic } from "./search-diagnostics.ts";
import { boundedText, exactKeys, finiteInteger, objectValue, sameUuid, validUuid } from "./wire-guards.ts";

export const TRANSCRIPT_JSON_MAX_BYTES = 16 * 1024 * 1024;
export const TRANSCRIPT_MAX_BYTES = 1024 * 1024 * 1024;
export const TRANSCRIPT_SORT_FIELDS = ["name", "size", "session", "indexing", "updated"] as const;
export type TranscriptSort = typeof TRANSCRIPT_SORT_FIELDS[number];
export const TRANSCRIPT_PAGE_SIZE = 50;
export const TRANSCRIPT_TEXT_PAGE_SIZE = 20000;
export type TranscriptStatus = "waiting" | "pending" | "processing" | "ready" | "failed";
export interface Transcript extends TranscriptNormalization {
  id: string;
  project_id: string;
  work_item_id: string | null;
  lease_generation_id: string | null;
  client: string;
  session_id: string | null;
  source_path: string;
  filename: string;
  kind: "primary" | "subagent" | "imported";
  status: TranscriptStatus;
  index_status: TranscriptStatus;
  index_error_code: string | null;
  copy_status: "pending" | "processing" | "ready" | "failed";
  copy_error_code: string | null;
  copied_at: string | null;
  indexing_started_at: string | null;
  indexing_completed_at: string | null;
  last_updated_at?: string | null;
  index_created_at?: string | null;
  session_ids?: string[];
  models?: string[];
  error_code: string | null;
  size_bytes: number | null;
  mime_type: string | null;
  format: string | null;
  sha256: string | null;
  text_sha256: string | null;
  metadata: Record<string, string[]>;
  truncated: boolean;
  created_at: string;
  snippet?: string | null;
  score?: number | null;
  rank: number | null;
  score_type: ScoreType;
  matched_fields: ("metadata" | "content")[];
}
export interface TranscriptPage extends SearchDisclosure, SearchRanking, SearchPagination {
  unsegmented_content_omitted: number;
  detail: "full";
  term_diagnostics: TermDiagnostic[];
  items: Transcript[];
  total: number;
  limit: number;
  offset: number;
  indexing_incomplete: boolean;
}
export interface TranscriptSettings {
  enabled: boolean;
  max_file_size_bytes: number;
  revision: number;
  allowed_roots: string[];
  operator_max_file_size_bytes: number;
}
export interface TranscriptText {
  transcript_id: string;
  project_id: string;
  next_offset: number | null;
  text: string;
  total_chars: number;
  offset: number;
  limit: number;
  status: TranscriptStatus;
  truncated: boolean;
  text_sha256: string;
  normalized_revision: string | null;
  segments: null;
  segment_window: null;
  next_segment_id: null;
  next_segment_offset: null;
  next_segment_after: null;
}
const statuses = ["waiting", "pending", "processing", "ready", "failed"];
const timestamp = (value: unknown) => typeof value === "string" && value.length <= 40 && Number.isFinite(Date.parse(value));
const nullableText = (value: unknown, max: number) => value === null || boundedText(value, max);
export const transcriptDigest = (value: unknown): value is string => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);

export function decodeTranscript(value: unknown, projectId: string, transcriptId?: string): Transcript {
  const row = objectValue(value);
  const metadata = objectValue(row?.metadata);
  if (!row || !validScoreType(row.score_type) || !(row.rank === null || finiteInteger(row.rank, 1))
    || !Array.isArray(row.matched_fields) || row.matched_fields.length > 2 || new Set(row.matched_fields).size !== row.matched_fields.length || row.matched_fields.some((field) => !["metadata", "content"].includes(String(field)))
    || row.segment_id != null && !(row.matched_fields as string[]).includes("content")
    || !validTranscriptNormalization(row) || !validUuid(row.id) || !sameUuid(row.project_id, projectId)
    || transcriptId !== undefined && !sameUuid(row.id, transcriptId)
    || (row.kind === "imported"
      ? row.work_item_id !== null || row.lease_generation_id !== null || row.session_id !== null
      : !validUuid(row.work_item_id) || !validUuid(row.lease_generation_id))
    || !boundedText(row.client, 200) || !nullableText(row.session_id, 200)
    || !boundedText(row.source_path, 4096) || !boundedText(row.filename, 4096)
    || !["primary", "subagent", "imported"].includes(row.kind as string) || !statuses.includes(row.status as string)
    || !statuses.includes(row.index_status as string) || !nullableText(row.index_error_code, 200)
    || !["pending", "processing", "ready", "failed"].includes(row.copy_status as string)
    || !nullableText(row.copy_error_code, 200) || !(row.copied_at === null || timestamp(row.copied_at))
    || !(row.indexing_started_at === null || timestamp(row.indexing_started_at))
    || !(row.indexing_completed_at === null || timestamp(row.indexing_completed_at))
    || ![row.last_updated_at, row.index_created_at].every((value) => value == null || timestamp(value))
    || ![row.session_ids, row.models].every((value) => value === undefined || Array.isArray(value) && value.length <= 8 && value.every((item) => boundedText(item, 200)))
    || !nullableText(row.error_code, 200) || !(row.size_bytes === null || finiteInteger(row.size_bytes))
    || !nullableText(row.mime_type, 200) || !nullableText(row.format, 200)
    || !(row.sha256 === null || transcriptDigest(row.sha256))
    || !(row.text_sha256 === null || transcriptDigest(row.text_sha256))
    || !timestamp(row.created_at) || typeof row.truncated !== "boolean"
    || !metadata || Object.keys(metadata).length > 64
    || !Object.entries(metadata).every(([key, values]) => boundedText(key, 128) && Array.isArray(values)
      && values.length <= 8 && values.every((item) => typeof item === "string" && Array.from(item).length <= 512))
    || !(row.snippet === undefined || row.snippet === null || typeof row.snippet === "string" && row.snippet.length <= 2000)
    || !(row.score === undefined || row.score === null || typeof row.score === "number" && Number.isFinite(row.score) && row.score >= 0)) {
    throw new Error("Mnemonic returned invalid transcript metadata.");
  }
  return row as unknown as Transcript;
}

export function decodeTranscriptPage(value: unknown, projectId: string, offset = 0, fulltext = false, workItemId?: string, contentKinds?: TranscriptContentKind[], queryMode: QueryMode = "terms"): TranscriptPage {
  const page = objectValue(value);
  if (!page || page.detail !== "full" || !Array.isArray(page.items) || !finiteInteger(page.total) || page.limit !== TRANSCRIPT_PAGE_SIZE
    || page.offset !== offset || !validSearchPagination(page)
    || typeof page.indexing_incomplete !== "boolean") throw new Error("Mnemonic returned an invalid transcript listing.");
  const disclosure = decodeSearchDisclosure(page, projectId, ["transcripts"]);
  decodeTermDiagnostics(page.term_diagnostics, page.total as number, ["transcripts"], disclosure.diagnostics, disclosure.query_interpretation.q);
  validateSearchDisclosure(disclosure, "transcripts", { work_item_id: workItemId ?? null, content_kinds: contentKinds ?? null }, fulltext, undefined, queryMode);
  const ranking = decodeSearchRanking(page);
  validateSourceRanking(ranking, disclosure.query_interpretation.q, "transcripts", disclosure.query_interpretation.transcripts?.match_mode);
  if (!finiteInteger(page.unsegmented_content_omitted) || page.unsegmented_content_omitted > 0 && (!page.indexing_incomplete || !fulltext || queryMode === "terms" && !disclosure.query_interpretation.q.includes('"'))) throw new Error("Mnemonic returned invalid transcript search coverage.");
  const items = page.items.map((item) => {
    const transcript = decodeTranscript(item, projectId);
    decodeHitRanking({ ...transcript, score: transcript.score ?? 0 }, ranking.score_type, page.total as number);
    if (!fulltext && transcript.matched_fields.includes("content")) throw new Error("Mnemonic returned content outside the requested search scope.");
    return transcript;
  });
  if (new Set(items.map((item) => item.id.toLowerCase())).size !== items.length
    || !fulltext && items.some((item) => item.snippet != null || item.segment_id != null)
    || contentKinds && items.some((item) => (item.segment_id != null || item.matched_fields.includes("content")) && (!item.segment_id || !contentKinds.includes(item.content_kind!)))
    || !page.indexing_incomplete && items.some((item) => item.normalization_status !== "ready" || item.normalization_incomplete
      || item.status !== "ready" || item.copy_status !== "ready" || item.index_status !== "ready" || item.truncated)
    || workItemId && items.some((item) => !sameUuid(item.work_item_id, workItemId))) {
    throw new Error("Mnemonic returned transcripts outside the requested scope.");
  }
  return { ...page, ...ranking, items } as TranscriptPage;
}

export function decodeTranscriptSettings(value: unknown): TranscriptSettings {
  const row = objectValue(value);
  if (!row || !exactKeys(row, ["enabled", "max_file_size_bytes", "revision", "allowed_roots", "operator_max_file_size_bytes"])
    || typeof row.enabled !== "boolean" || !finiteInteger(row.max_file_size_bytes, 1, TRANSCRIPT_MAX_BYTES)
    || !finiteInteger(row.operator_max_file_size_bytes, 1, TRANSCRIPT_MAX_BYTES) || row.max_file_size_bytes > row.operator_max_file_size_bytes
    || !finiteInteger(row.revision, 1) || !Array.isArray(row.allowed_roots) || row.allowed_roots.length > 100
    || !row.allowed_roots.every((path) => boundedText(path, 4096))) throw new Error("Mnemonic returned invalid transcript settings.");
  return row as unknown as TranscriptSettings;
}

export function decodeTranscriptText(value: unknown, transcriptId: string, digest: string, offset: number, projectId: string): TranscriptText {
  const row = objectValue(value);
  if (!row || !sameUuid(row.transcript_id, transcriptId) || !sameUuid(row.project_id, projectId) || row.text_sha256 !== digest
    || typeof row.text !== "string" || Array.from(row.text).length > TRANSCRIPT_TEXT_PAGE_SIZE
    || !finiteInteger(row.total_chars) || row.offset !== offset || row.limit !== TRANSCRIPT_TEXT_PAGE_SIZE
    || Array.from(row.text).length !== Math.min(TRANSCRIPT_TEXT_PAGE_SIZE, Math.max(0, row.total_chars - offset))
    || row.next_offset !== (offset + TRANSCRIPT_TEXT_PAGE_SIZE < row.total_chars ? offset + TRANSCRIPT_TEXT_PAGE_SIZE : null)
    || row.status !== "ready" || typeof row.truncated !== "boolean"
    || !(row.normalized_revision === null || transcriptDigest(row.normalized_revision))
    || row.segments !== null || row.segment_window !== null || row.next_segment_id !== null
    || row.next_segment_offset !== null || row.next_segment_after !== null) throw new Error("The transcript changed or its text response is invalid. Close and reopen it to load the latest index.");
  return row as unknown as TranscriptText;
}

export function transcriptPath(projectId: string, transcriptId?: string): string {
  if (!validUuid(projectId) || transcriptId !== undefined && !validUuid(transcriptId)) throw new Error("Invalid transcript identity.");
  return `/api/transcripts/projects/${projectId}/transcripts${transcriptId ? `/${transcriptId}` : ""}`;
}
export function transcriptSettingsPath(projectId: string): string {
  if (!validUuid(projectId)) throw new Error("Invalid transcript project.");
  return `/api/transcripts/projects/${projectId}/transcript-settings`;
}
export function transcriptLibraryPath(projectId: string, workItemId?: string): string {
  if (!validUuid(projectId) || workItemId !== undefined && !validUuid(workItemId)) throw new Error("Invalid transcript project.");
  return `/transcripts?${new URLSearchParams({ project: projectId, ...(workItemId ? { work: workItemId } : {}) })}`;
}
export function transcriptContentPath(transcript: Transcript): string {
  if (!transcript.text_sha256) throw new Error("Transcript text is unavailable.");
  return `${transcriptPath(transcript.project_id, transcript.id)}/content?expected_sha256=${transcript.text_sha256}`;
}
export async function transcriptRequest(path: string, init: RequestInit = {}): Promise<unknown> {
  const response = await fetch(path, { ...init, credentials: "same-origin", cache: "no-store", headers: { Accept: "application/json", ...(init.body ? { "Content-Type": "application/json" } : {}), ...init.headers } });
  const value = await readBoundedJson(response, TRANSCRIPT_JSON_MAX_BYTES);
  if (!response.ok) {
    const detail = objectValue(value)?.detail;
    const message = typeof detail === "string" ? detail : objectValue(detail)?.message;
    throw new Error(typeof message === "string" ? message : "Unable to load transcripts.");
  }
  return value;
}
export function transcriptStatusLabel(transcript: Transcript): string {
  if (transcript.copy_status === "failed") return transcript.status === "ready" ? "Indexed · Copy failed" : "Copy failed";
  if (transcript.copy_status === "processing") return transcript.status === "ready" ? "Indexed · Copying" : "Copying";
  if (transcript.copy_status === "pending" && transcript.status !== "waiting") return transcript.status === "ready" ? "Indexed · Copy queued" : "Copy queued";
  if (transcript.status === "ready" && transcript.index_status !== "ready") {
    const replacement = { waiting: "Reindex queued", pending: "Reindex queued", processing: "Reindexing", failed: "Reindex failed" };
    return `Indexed · ${replacement[transcript.index_status]}`;
  }
  const labels = { waiting: transcript.kind === "imported" ? "Queued" : "Waiting for work to leave Active", pending: "Queued", processing: "Indexing", ready: "Indexed", failed: "Failed" };
  const notes = [transcript.truncated ? "Search text limited" : "",
    transcriptCoverageLabel(transcript),
    transcript.metadata["transcript:metadata_limited"]?.includes("true") ? "Metadata limited" : ""].filter(Boolean);
  return [labels[transcript.status], ...notes].join(" · ");
}

// Only these exact proxy-owned responses prove a fresh request was never dispatched.
// Upstream failures and unknown envelopes cannot dismiss an uncertain rebuild.
export const TRANSCRIPT_PROXY_REJECTION_MESSAGES: Readonly<Record<number, readonly string[]>> = {
  400: ["The request contains a forbidden control header.", "Invalid transcript query.", "Invalid transcript settings request.", "Invalid or oversized transcript settings request.", "Transcript reads do not accept a body."],
  403: ["This dashboard request is not from a trusted origin."],
  404: ["Route not found."],
  415: ["Encoded requests are not supported.", "Send transcript settings as JSON."]
};
export function decodeTranscriptProxyRejection(status: number, value: unknown): string | null {
  const root = objectValue(value);
  const detail = objectValue(root?.detail);
  if (!root || !exactKeys(root, ["detail"]) || !detail || !exactKeys(detail, ["code", "message"])
    || detail.code !== "transcript_proxy_rejected" || typeof detail.message !== "string"
    || !TRANSCRIPT_PROXY_REJECTION_MESSAGES[status]?.includes(detail.message)) return null;
  return detail.message;
}


export function validTranscriptDirectory(value: unknown): value is string {
  return boundedText(value, 4096) && value.startsWith("/") && !value.split("/").includes("..")
    && !Array.from(value).some((char) => char.codePointAt(0)! < 32 || char.codePointAt(0)! >= 0xd800 && char.codePointAt(0)! <= 0xdfff);
}

export interface TranscriptImport {
  project_id: string;
  client_operation_id: string;
  directory: string;
  imported: number;
  existing: number;
  skipped: number;
}
export function decodeTranscriptImport(value: unknown, projectId: string, operationId: string, directory: string): TranscriptImport {
  const row = objectValue(value);
  if (!row || !exactKeys(row, ["project_id", "client_operation_id", "directory", "imported", "existing", "skipped"])
    || !sameUuid(row.project_id, projectId) || !sameUuid(row.client_operation_id, operationId) || row.directory !== directory
    || !finiteInteger(row.imported, 0, 5000) || !finiteInteger(row.existing, 0, 5000) || row.imported + row.existing > 5000
    || !finiteInteger(row.skipped, 0, 50000)) throw new Error("Import outcome is uncertain. Retry the preserved request.");
  return row as unknown as TranscriptImport;
}

export function decodeTranscriptImportRejection(status: number, value: unknown): string | null {
  const detail = objectValue(objectValue(value)?.detail);
  // These scan failures happen before any registration. Only a fresh request
  // may dismiss them; a failed retry cannot settle an earlier uncertain attempt.
  if (status !== 422 || !detail || !["transcript_import_path_not_allowed", "transcript_import_scan_failed", "transcript_import_scan_limit"].includes(String(detail.code))
    || !boundedText(detail.message, 1000)) return null;
  return detail.message;
}

export function transcriptClientLabel(client: string): string {
  const normalized = client.trim().toLowerCase();
  if (["claude-code", "claude_code", "claude code"].includes(normalized)) return "Claude Code";
  if (["codex", "openai-codex", "openai_codex", "openai codex", "codex-cli", "codex_cli", "codex cli"].includes(normalized)) return "OpenAI Codex";
  return client;
}
