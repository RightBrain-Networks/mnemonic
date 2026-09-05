import type { ProjectActivityItem, ProjectActivityPage } from "./types.ts";
import { decimalString, decodePhase12Cursor, type ActivityCursor } from "./activity-cursors.ts";
import { exactKeys, objectValue, sameUuid, validUtcDateTime, validUuid } from "./wire-guards.ts";

const REF_MATRIX: Record<ProjectActivityItem["kind"], readonly string[]> = {
  work_event: ["work_event_id", "event_type", "work_item_id"],
  project_created: [], project_updated: [], project_settings_updated: ["settings_revision"],
  lease_renewed: ["work_item_id", "lease_generation_id"],
  job_completion_report_created: ["work_item_id", "job_completion_report_id"],
  job_completion_report_dismissed: ["work_item_id", "job_completion_report_id", "human_dismissal_id"],
  job_completion_report_follow_up_created: ["work_item_id", "job_completion_report_id", "follow_up_id"]
};
const EVENT_TYPES = new Set([
  "work_created", "work_updated", "work_status_changed", "work_reopened", "work_claimed",
  "work_released", "checkpoint_added", "progress", "dependency_added", "dependency_removed",
  "relationship_added", "relationship_removed", "human_attention_requested", "human_attention_resolved",
  "work_merged", "work_completed", "work_deleted"
]);
const REF_FIELDS = ["work_event_id", "event_type", "work_item_id", "job_completion_report_id",
  "human_dismissal_id", "follow_up_id", "settings_revision", "lease_generation_id"];
const ACTIVITY_FIELDS = ["sequence", "kind", ...REF_FIELDS, "recorded_at", "origin"] as const;
const ACTIVITY_PAGE_FIELDS = ["project_id", "stream_id", "items", "next_cursor", "has_more", "through_sequence", "historical_through_sequence", "historical_coverage"] as const;
export const PROJECT_ACTIVITY_DECODER_FIELDS = { decodeActivityItem: ACTIVITY_FIELDS, decodeActivityPage: ACTIVITY_PAGE_FIELDS } as const;
const invalid = () => new Error("Mnemonic returned an invalid project activity response.");
export function decodeActivityItem(value: unknown): ProjectActivityItem {
  const item = objectValue(value);
  if (!item || !exactKeys(item, ACTIVITY_FIELDS)
    || !decimalString(item.sequence, true) || typeof item.kind !== "string"
    || !Object.hasOwn(REF_MATRIX, item.kind) || !validUtcDateTime(item.recorded_at)
    || !["live", "history_import"].includes(String(item.origin))
    || item.origin === "history_import" && item.kind !== "work_event") throw invalid();
  const required = REF_MATRIX[item.kind as ProjectActivityItem["kind"]];
  for (const field of REF_FIELDS) {
    if (!required.includes(field)) { if (item[field] !== null) throw invalid(); continue; }
    if (field === "event_type" ? !EVENT_TYPES.has(String(item[field]))
      : field === "work_event_id" || field === "settings_revision" ? !decimalString(item[field], true)
        : !validUuid(item[field])) throw invalid();
  }
  if (new TextEncoder().encode(JSON.stringify(item)).byteLength > 4_096) throw invalid();
  return item as unknown as ProjectActivityItem;
}
export function decodeActivityPage(value: unknown, projectId: string, request: {
  after?: string; start?: "now"; limit?: number
} = {}): ProjectActivityPage {
  const page = objectValue(value);
  if (!page || !exactKeys(page, ACTIVITY_PAGE_FIELDS)
    || !sameUuid(page.project_id, projectId) || !validUuid(page.stream_id)
    || !Array.isArray(page.items) || page.items.length > (request.limit ?? 50)
    || typeof page.has_more !== "boolean" || !decimalString(page.through_sequence)
    || !decimalString(page.historical_through_sequence) || BigInt(page.historical_through_sequence) > BigInt(page.through_sequence)
    || page.historical_coverage !== "recorded_work_events_only") throw invalid();
  const next = decodePhase12Cursor(page.next_cursor, projectId, "activity") as ActivityCursor;
  const previous = request.after ? decodePhase12Cursor(request.after, projectId, "activity") as ActivityCursor : null;
  if (next.stream_id !== page.stream_id || previous && previous.stream_id !== page.stream_id) throw invalid();
  const items = page.items.map(decodeActivityItem);
  let sequence = request.start === "now" ? BigInt(page.through_sequence) : BigInt(previous?.after ?? "0");
  if (request.start === "now" && items.length) throw invalid();
  for (const item of items) {
    if (BigInt(item.sequence) !== sequence + 1n || BigInt(item.sequence) > BigInt(page.through_sequence)
      || (item.origin === "history_import") !== (BigInt(item.sequence) <= BigInt(page.historical_through_sequence))) throw invalid();
    sequence = BigInt(item.sequence);
  }
  if (next.after !== sequence.toString() || page.has_more !== (sequence < BigInt(page.through_sequence))) throw invalid();
  return { ...page, items } as unknown as ProjectActivityPage;
}
export interface ActivityInvalidations { work: boolean; reports: boolean; settings: boolean; projects: boolean; }
export function activityInvalidations(items: readonly ProjectActivityItem[]): ActivityInvalidations {
  const result: ActivityInvalidations = { work: false, reports: false, settings: false, projects: false };
  for (const item of items) {
    if (item.kind === "work_event" || item.kind === "lease_renewed") result.work = true;
    // Report envelopes include current source lifecycle, deletion and canonical identity.
    if (item.kind === "work_event" || item.kind.startsWith("job_completion_report_")) result.reports = true;
    if (item.kind === "project_settings_updated") result.settings = true;
    if (item.kind === "project_created" || item.kind === "project_updated") result.projects = true;
  }
  return result;
}
