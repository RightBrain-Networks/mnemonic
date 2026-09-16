import { boundedText, exactKeys, finiteInteger, objectValue, sameUuid } from "./wire-guards.ts";

export const TRANSCRIPT_CONTENT_KINDS = ["human_text", "assistant_text", "tool_call", "tool_result", "system_text", "reasoning", "summary", "unsupported"] as const;
export type TranscriptContentKind = typeof TRANSCRIPT_CONTENT_KINDS[number];
export const TRANSCRIPT_CONTENT_LABELS: Record<TranscriptContentKind, string> = {
  human_text: "Human messages", assistant_text: "Assistant messages", tool_call: "Tool calls",
  tool_result: "Tool results", system_text: "System messages", reasoning: "Reasoning",
  summary: "Summaries", unsupported: "Unsupported content"
};
export const NORMALIZED_SEGMENT_OFFSET_MAX = 1_073_741_824;
const TEXT_LIMIT = 20_000;
const digest = (value: unknown): value is string => typeof value === "string" && /^[0-9a-f]{64}$/.test(value);
export const segmentIdentity = (value: unknown): value is string => typeof value === "string" && /^[0-9a-f]{24}$/.test(value);
export const validContentKinds = (value: unknown): value is TranscriptContentKind[] => Array.isArray(value)
  && value.length >= 1 && value.length <= 8 && new Set(value).size === value.length
  && value.every((kind) => TRANSCRIPT_CONTENT_KINDS.includes(kind));

export type TranscriptNormalization = {
  normalization_status: "pending" | "processing" | "ready" | "failed";
  normalization_error_code: string | null; normalized_revision: string | null;
  normalized_sha256: string | null; normalization_schema_version: number | null;
  normalized_size_bytes: number | null; normalizer_version: number | null;
  segment_count: number; normalization_incomplete: boolean;
  segment_id: string | null; content_kind: TranscriptContentKind | null;
};
export function validTranscriptNormalization(row: Record<string, unknown>): boolean {
  return ["pending", "processing", "ready", "failed"].includes(String(row.normalization_status))
    && (row.normalization_error_code === null || boundedText(row.normalization_error_code, 100))
    && (row.normalized_revision === null || digest(row.normalized_revision))
    && (row.normalized_sha256 === null || digest(row.normalized_sha256))
    && (row.normalization_schema_version === null || finiteInteger(row.normalization_schema_version, 1))
    && (row.normalizer_version === null || finiteInteger(row.normalizer_version, 1))
    && (row.normalized_size_bytes === null || finiteInteger(row.normalized_size_bytes))
    && finiteInteger(row.segment_count) && typeof row.normalization_incomplete === "boolean"
    && (row.segment_id === null ? row.content_kind === null : segmentIdentity(row.segment_id)
      && digest(row.normalized_revision) && TRANSCRIPT_CONTENT_KINDS.includes(row.content_kind as TranscriptContentKind)
      && typeof row.snippet === "string" && row.snippet.length > 0);
}

type JsonValue = string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue };
export type TranscriptSegment = {
  segment_id: string; event_id: string; ordinal: number; source_record: number; source_block: string;
  role: string | null; content_kind: TranscriptContentKind; text: string; timestamp: string | null;
  native_event_id: string | null; native_parent_id: string | null; native_branch_id: string | null;
  channel: string | null; is_sidechain: boolean | null; is_error: boolean | null;
  tool_name: string | null; call_id: string | null; payload: JsonValue; dispositions: string[];
  related_segment_id: string | null; text_offset: number; text_truncated: boolean;
};
export type TranscriptSegmentWindow = {
  anchor_segment_id: string; anchor_ordinal: number; first_ordinal: number; last_ordinal: number;
};
export type TranscriptSegmentLocator = {
  segmentId: string; revision: string; offset: number; before: number; after: number;
};
export type TranscriptSegmentPage = {
  project_id: string; transcript_id: string; text_sha256: string | null; normalized_revision: string;
  text: string; total_chars: number; offset: number; limit: number; next_offset: null;
  status: "waiting" | "pending" | "processing" | "ready" | "failed"; truncated: boolean;
  segments: TranscriptSegment[]; segment_window: TranscriptSegmentWindow;
  next_segment_id: string | null; next_segment_offset: number | null; next_segment_after: number | null;
};
const nativeKeys = ["role", "timestamp", "native_event_id", "native_parent_id", "native_branch_id", "channel", "tool_name", "call_id"] as const;
const segmentKeys = ["segment_id", "event_id", "ordinal", "source_record", "source_block", "content_kind", "text", ...nativeKeys, "is_sidechain", "is_error", "payload", "dispositions", "related_segment_id", "text_offset", "text_truncated"];
const pageKeys = ["project_id", "transcript_id", "text_sha256", "normalized_revision", "text", "total_chars", "offset", "limit", "next_offset", "status", "truncated", "segments", "segment_window", "next_segment_id", "next_segment_offset", "next_segment_after"];
const windowKeys = ["anchor_segment_id", "anchor_ordinal", "first_ordinal", "last_ordinal"];
function invalid(): never { throw new Error("The transcript revision changed or its conversation response is invalid. Reopen the search result."); }
function charLength(value: string): number { return Array.from(value).length; }
function validJson(value: unknown): boolean {
  if (value === null || typeof value === "string" || typeof value === "boolean") return true;
  if (typeof value === "number") return Number.isFinite(value);
  if (Array.isArray(value)) return value.every(validJson);
  const row = objectValue(value);
  return row !== null && Object.values(row).every(validJson);
}
async function identity(value: string): Promise<string> {
  const bytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return Array.from(new Uint8Array(bytes), (byte) => byte.toString(16).padStart(2, "0")).join("").slice(0, 24);
}
async function decodeSegment(value: unknown, revision: string): Promise<TranscriptSegment> {
  const row = objectValue(value);
  if (!row || !exactKeys(row, segmentKeys) || !segmentIdentity(row.segment_id) || !segmentIdentity(row.event_id)
    || !finiteInteger(row.ordinal) || !finiteInteger(row.source_record, 1) || !boundedText(row.source_block, 64) || row.source_block === ""
    || !TRANSCRIPT_CONTENT_KINDS.includes(row.content_kind as TranscriptContentKind)
    || typeof row.text !== "string" || charLength(row.text) > TEXT_LIMIT
    || !nativeKeys.every((key) => row[key] === null || typeof row[key] === "string" && charLength(row[key]) <= 4096)
    || !["is_sidechain", "is_error"].every((key) => row[key] === null || typeof row[key] === "boolean")
    || !validJson(row.payload) || !Array.isArray(row.dispositions) || row.dispositions.length > 16
    || !row.dispositions.every((value) => boundedText(value, 80) && value !== "")
    || !(row.related_segment_id === null || segmentIdentity(row.related_segment_id))
    || !finiteInteger(row.text_offset, 0, NORMALIZED_SEGMENT_OFFSET_MAX) || typeof row.text_truncated !== "boolean") invalid();
  const eventId = await identity(`${revision}:${row.source_record}`);
  if (row.event_id !== eventId || row.segment_id !== await identity(`${eventId}:${row.source_block}`)) invalid();
  return row as unknown as TranscriptSegment;
}
export function validSegmentLocator(value: TranscriptSegmentLocator): boolean {
  return segmentIdentity(value.segmentId) && digest(value.revision)
    && finiteInteger(value.offset, 0, NORMALIZED_SEGMENT_OFFSET_MAX)
    && finiteInteger(value.before, 0, 20) && finiteInteger(value.after, 0, 20)
    && value.before + value.after <= 20 && !(value.before && value.offset);
}
export function transcriptSegmentQuery(locator: TranscriptSegmentLocator, textDigest?: string | null): URLSearchParams {
  if (!validSegmentLocator(locator) || textDigest != null && !digest(textDigest)) invalid();
  return new URLSearchParams({ segment_id: locator.segmentId, expected_normalized_revision: locator.revision,
    offset: String(locator.offset), before: String(locator.before), after: String(locator.after), limit: String(TEXT_LIMIT),
    ...(textDigest ? { expected_sha256: textDigest } : {}) });
}
function validContinuation(page: TranscriptSegmentPage): boolean {
  const last = page.segments.at(-1)!;
  if (last.text_truncated) return last.text.length > 0 && page.next_segment_id === last.segment_id
    && page.next_segment_offset === last.text_offset + charLength(last.text)
    && page.next_segment_after === page.segment_window.last_ordinal - last.ordinal;
  if (last.ordinal < page.segment_window.last_ordinal) return segmentIdentity(page.next_segment_id)
    && !page.segments.some((segment) => segment.segment_id === page.next_segment_id)
    && page.next_segment_offset === 0 && page.next_segment_after === page.segment_window.last_ordinal - last.ordinal - 1;
  return page.next_segment_id === null && page.next_segment_offset === null && page.next_segment_after === null;
}
function segmentsFitBudget(segments: TranscriptSegment[]): boolean {
  const jsonBytes = (value: unknown) => new TextEncoder().encode(JSON.stringify(value)).length;
  let contentSize = 2 * (segments.length - 1);
  let metadataSize = 0;
  for (const segment of segments) {
    const native = Object.fromEntries(nativeKeys.filter((key) => segment[key] !== null).map((key) => [key, segment[key]]));
    if (segment.dispositions.includes("metadata_omitted_for_budget") && Object.keys(native).length
      || segment.dispositions.includes("payload_omitted_for_budget") && segment.payload !== null) return false;
    if (!segment.dispositions.includes("metadata_omitted_for_budget")) metadataSize += jsonBytes(native);
    contentSize += charLength(segment.text) + (segment.payload === null ? 0 : jsonBytes(segment.payload));
  }
  return contentSize <= TEXT_LIMIT && metadataSize <= 4096;
}
export async function decodeTranscriptSegments(
  value: unknown, projectId: string, transcriptId: string, locator: TranscriptSegmentLocator, textDigest?: string | null
): Promise<TranscriptSegmentPage> {
  const row = objectValue(value);
  const window = objectValue(row?.segment_window);
  if (!validSegmentLocator(locator) || !row || !exactKeys(row, pageKeys)
    || !sameUuid(row.project_id, projectId) || !sameUuid(row.transcript_id, transcriptId)
    || row.normalized_revision !== locator.revision || !(row.text_sha256 === null || digest(row.text_sha256))
    || textDigest != null && row.text_sha256 !== textDigest
    || typeof row.text !== "string" || charLength(row.text) > TEXT_LIMIT || !finiteInteger(row.total_chars)
    || charLength(row.text) > row.total_chars || row.offset !== locator.offset || row.limit !== TEXT_LIMIT
    || row.next_offset !== null || !["waiting", "pending", "processing", "ready", "failed"].includes(String(row.status))
    || typeof row.truncated !== "boolean" || !Array.isArray(row.segments) || row.segments.length < 1 || row.segments.length > 21
    || !window || !exactKeys(window, windowKeys) || window.anchor_segment_id !== locator.segmentId
    || !finiteInteger(window.anchor_ordinal) || !finiteInteger(window.first_ordinal) || !finiteInteger(window.last_ordinal)
    || window.first_ordinal !== Math.max(0, window.anchor_ordinal - locator.before)
    || window.last_ordinal < window.anchor_ordinal || window.last_ordinal > window.anchor_ordinal + locator.after
    || row.segments.length > window.last_ordinal - window.first_ordinal + 1
    || !(row.next_segment_id === null || segmentIdentity(row.next_segment_id))
    || !(row.next_segment_offset === null || finiteInteger(row.next_segment_offset, 0, NORMALIZED_SEGMENT_OFFSET_MAX))
    || !(row.next_segment_after === null || finiteInteger(row.next_segment_after, 0, 20))) invalid();
  const selectedWindow = window as unknown as TranscriptSegmentWindow;
  const segments = await Promise.all(row.segments.map((value) => decodeSegment(value, locator.revision)));
  if (new Set(segments.map((segment) => segment.segment_id)).size !== segments.length
    || segments.some((segment, index) => segment.ordinal !== selectedWindow.first_ordinal + index
      || segment.text_offset !== (segment.ordinal === selectedWindow.anchor_ordinal ? locator.offset : 0)
      || segment.ordinal === selectedWindow.anchor_ordinal && segment.segment_id !== locator.segmentId
      || index < segments.length - 1 && segment.text_truncated)
    || row.text !== segments.map((segment) => segment.text).join("\n\n")) invalid();
  const page = { ...row, segments } as unknown as TranscriptSegmentPage;
  if (!segmentsFitBudget(segments) || !validContinuation(page) || page.next_segment_id === null && charLength(page.text) !== page.total_chars) invalid();
  return page;
}
export function nextTranscriptSegment(page: TranscriptSegmentPage): TranscriptSegmentLocator | null {
  return page.next_segment_id === null ? null : { segmentId: page.next_segment_id, revision: page.normalized_revision,
    offset: page.next_segment_offset!, before: 0, after: page.next_segment_after! };
}
