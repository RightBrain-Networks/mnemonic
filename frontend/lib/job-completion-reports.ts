import type {
  JobCompletionReport, JobCompletionReportInput, JobReportCount, JobReportDetail,
  JobReportDismissal, JobReportEnvelope, JobReportFollowUp, JobReportPage,
  JobReportProvenancePage, JobReportSourceState, ProjectSettings, WorkItem
} from "./types.ts";
import {
  boundedText, exactKeys, finiteInteger, jsonEqual, nullableBoundedText, nullableUuid,
  objectValue, sameUuid, validUnicode, validUtcDateTime, validUuid,
  compareUtcDateTimes
} from "./wire-guards.ts";

import {
  decimalString,
  validOpaqueCursor,
  decodePhase12Cursor,
  type ProvenanceCursor,
  type ReportsCursor
} from "./activity-cursors.ts";
export { decimalString, validOpaqueCursor } from "./activity-cursors.ts";
const encoder = new TextEncoder();
const FORBIDDEN_PROSE = /[\p{Cc}\p{Cs}\u2028\u2029\u061c\u200e\u200f\u202a-\u202e\u2066-\u206f]/u;
const FORBIDDEN_PROMPT = /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f\p{Cs}\u061c\u200e\u200f\u202a-\u202e\u2066-\u206f]/u;
export const REPORT_FIELDS = [
  "id", "project_id", "work_item_id", "closeout_event_id", "closeout_work_version",
  "closeout_status", "completion_checkpoint_id", "work_title_at_closeout", "summary",
  "fyi_items", "actor_client", "actor_session_id", "actor_model", "prompt_revision",
  "prompt_sha256", "created_at"
] as const;
const PROJECT_SETTINGS_FIELDS = ["project_id", "recall_pointer_template", "job_completion_report_prompt", "revision"] as const;
const REPORT_ENVELOPE_FIELDS = ["report", "created_sequence", "human_dismissed", "human_dismissal", "source_work_state", "follow_up_count"] as const;
const REPORT_PAGE_FIELDS = ["project_id", "stream_id", "dismissal", "work_item_id", "as_of_sequence", "items", "has_more", "next_cursor"] as const;
const REPORT_COUNT_FIELDS = ["project_id", "undismissed_count", "as_of_sequence"] as const;
const REPORT_DISMISSAL_FIELDS = ["id", "actor_client", "actor_session_id", "actor_model", "created_at"] as const;
const REPORT_FOLLOW_UP_FIELDS = ["id", "project_id", "report_id", "created_sequence", "source_work_item_id", "follow_up_work_item_id", "actor_client", "actor_session_id", "actor_model", "created_at"] as const;
const REPORT_SOURCE_FIELDS = ["work_item_id", "status", "canonical_work_item_id", "deleted"] as const;
export const JOB_REPORT_DECODER_FIELDS = {
  decodeJobReport: REPORT_FIELDS,
  decodeReportDetail: [...REPORT_FIELDS, "authoring_prompt"],
  decodeProjectSettings: PROJECT_SETTINGS_FIELDS,
  decodeReportEnvelope: REPORT_ENVELOPE_FIELDS,
  decodeReportPage: REPORT_PAGE_FIELDS,
  decodeReportCount: REPORT_COUNT_FIELDS,
  decodeReportDismissal: REPORT_DISMISSAL_FIELDS,
  decodeReportFollowUp: REPORT_FOLLOW_UP_FIELDS,
  decodeSourceState: REPORT_SOURCE_FIELDS
} as const;
const invalid = () => new Error("Mnemonic returned an invalid job completion report response.");

export function validReportText(value: unknown, maxScalars: number, maxBytes: number): value is string {
  return typeof value === "string" && validUnicode(value) && Boolean(value.trim())
    && !FORBIDDEN_PROSE.test(value) && Array.from(value).length <= maxScalars
    && encoder.encode(value).byteLength <= maxBytes;
}
export function validReportPrompt(value: unknown): value is string {
  return typeof value === "string" && validUnicode(value) && Boolean(value.trim())
    && !FORBIDDEN_PROMPT.test(value) && Array.from(value).length <= 8_000
    && encoder.encode(value).byteLength <= 16_384;
}
export function validJobReportInput(value: unknown): value is JobCompletionReportInput {
  const report = objectValue(value);
  return Boolean(report && exactKeys(report, ["summary", "fyi_items", "prompt_revision"])
    && validReportText(report.summary, 2_000, 8_000)
    && Array.isArray(report.fyi_items) && report.fyi_items.length <= 10
    && report.fyi_items.every((item) => validReportText(item, 600, 2_400))
    && encoder.encode([report.summary, ...report.fyi_items].join("")).byteLength <= 16_384
    && decimalString(report.prompt_revision, true));
}
export function decodeProjectSettings(value: unknown, projectId: string): ProjectSettings {
  const settings = objectValue(value);
  if (!settings || !exactKeys(settings, PROJECT_SETTINGS_FIELDS) || !sameUuid(settings.project_id, projectId)
    || !nullableBoundedText(settings.recall_pointer_template, 100_000)
    || !validReportPrompt(settings.job_completion_report_prompt)
    || !decimalString(settings.revision, true)) {
    throw new Error("Mnemonic returned invalid project settings.");
  }
  return settings as unknown as ProjectSettings;
}
export function decodeJobReport(value: unknown, projectId: string, reportId?: string, detail = false): JobCompletionReport {
  const report = objectValue(value);
  if (!report || !exactKeys(report, [...REPORT_FIELDS, ...(detail ? ["authoring_prompt"] : [])])
    || !validUuid(report.id) || reportId !== undefined && !sameUuid(report.id, reportId)
    || !sameUuid(report.project_id, projectId) || !validUuid(report.work_item_id)
    || !decimalString(report.closeout_event_id, true) || !finiteInteger(report.closeout_work_version, 2)
    || !["done", "wont-do", "promoted"].includes(String(report.closeout_status))
    || !nullableUuid(report.completion_checkpoint_id)
    || (report.closeout_status === "done") !== (report.completion_checkpoint_id !== null)
    || !boundedText(report.work_title_at_closeout, 200)
    || !validJobReportInput({ summary: report.summary, fyi_items: report.fyi_items, prompt_revision: report.prompt_revision })
    || !boundedText(report.actor_client, 80) || !boundedText(report.actor_session_id, 200)
    || !nullableBoundedText(report.actor_model, 120)
    || typeof report.prompt_sha256 !== "string" || !/^[a-f0-9]{64}$/.test(report.prompt_sha256)
    || !validUtcDateTime(report.created_at) || detail && !validReportPrompt(report.authoring_prompt)) throw invalid();
  return report as unknown as JobCompletionReport;
}
export function matchCloseoutReport(value: unknown, projectId: string, work: WorkItem, input: unknown, actor: unknown, checkpointId: string | null): JobCompletionReport {
  if (!validJobReportInput(input)) throw new Error("The frozen job completion report is invalid.");
  const report = decodeJobReport(value, projectId);
  const expectedActor = objectValue(actor);
  if (!sameUuid(report.work_item_id, work.id) || report.closeout_status !== work.status
    || report.closeout_work_version !== work.version || report.work_title_at_closeout !== work.title
    || report.completion_checkpoint_id !== checkpointId || report.summary !== input.summary
    || !jsonEqual(report.fyi_items, input.fyi_items) || report.prompt_revision !== input.prompt_revision
    || report.actor_client !== (expectedActor?.actor_client ?? expectedActor?.source_client)
    || report.actor_session_id !== (expectedActor?.actor_session_id ?? expectedActor?.source_session_id)
    || report.actor_model !== (expectedActor?.actor_model ?? expectedActor?.source_model ?? null)) throw invalid();
  return report;
}
export function decodeReportDismissal(value: unknown, projectId: string, reportId: string): JobReportDismissal {
  const action = objectValue(value);
  if (!action || !exactKeys(action, REPORT_DISMISSAL_FIELDS)
    || !validUuid(action.id) || !boundedText(action.actor_client, 80)
    || !boundedText(action.actor_session_id, 200) || !nullableBoundedText(action.actor_model, 120)
    || !validUtcDateTime(action.created_at)) throw invalid();
  return action as unknown as JobReportDismissal;
}
function decodeSourceState(value: unknown, workItemId: string): JobReportSourceState {
  const source = objectValue(value);
  if (!source || !exactKeys(source, REPORT_SOURCE_FIELDS)
    || !sameUuid(source.work_item_id, workItemId)
    || !["pending", "deferred", "done", "wont-do", "promoted"].includes(String(source.status))
    || !validUuid(source.canonical_work_item_id) || typeof source.deleted !== "boolean") throw invalid();
  return source as unknown as JobReportSourceState;
}
export function decodeReportEnvelope(value: unknown, projectId: string, reportId?: string, detail = false): JobReportEnvelope {
  const envelope = objectValue(value);
  if (!envelope || !exactKeys(envelope, REPORT_ENVELOPE_FIELDS)
    || !decimalString(envelope.created_sequence, true) || typeof envelope.human_dismissed !== "boolean" || !decimalString(envelope.follow_up_count)
    || envelope.human_dismissed !== (envelope.human_dismissal !== null)) throw invalid();
  const report = decodeJobReport(envelope.report, projectId, reportId, detail);
  return {
    report, created_sequence: envelope.created_sequence, human_dismissed: envelope.human_dismissed,
    human_dismissal: envelope.human_dismissal === null ? null : decodeReportDismissal(envelope.human_dismissal, projectId, report.id),
    source_work_state: decodeSourceState(envelope.source_work_state, report.work_item_id),
    follow_up_count: envelope.follow_up_count
  };
}
export function decodeReportDetail(value: unknown, projectId: string, reportId: string): JobReportDetail {
  return decodeReportEnvelope(value, projectId, reportId, true) as JobReportDetail;
}
export function decodeReportPage(value: unknown, projectId: string, expected: {
  dismissal?: JobReportPage["dismissal"]; workItemId?: string; limit?: number; previous?: JobReportPage
} = {}): JobReportPage {
  const page = objectValue(value);
  if (!page || !exactKeys(page, REPORT_PAGE_FIELDS)
    || !sameUuid(page.project_id, projectId) || !validUuid(page.stream_id)
    || page.dismissal !== (expected.dismissal ?? "undismissed")
    || (expected.workItemId ? !sameUuid(page.work_item_id, expected.workItemId) : page.work_item_id !== null)
    || !decimalString(page.as_of_sequence) || !Array.isArray(page.items)
    || page.items.length > (expected.limit ?? 20) || typeof page.has_more !== "boolean"
    || (page.has_more ? !validOpaqueCursor(page.next_cursor) || page.items.length === 0 : page.next_cursor !== null)
    || expected.previous && (page.stream_id !== expected.previous.stream_id || page.as_of_sequence !== expected.previous.as_of_sequence
      || page.next_cursor !== null && page.next_cursor === expected.previous.next_cursor)) throw invalid();
  const items = page.items.map((item) => decodeReportEnvelope(item, projectId));
  if (new Set(items.map((item) => item.report.id)).size !== items.length
    || items.some((item) => expected.workItemId && !sameUuid(item.report.work_item_id, expected.workItemId)
      || page.dismissal === "undismissed" && item.human_dismissed || page.dismissal === "dismissed" && !item.human_dismissed)) throw invalid();
  let previousSequence = BigInt(page.as_of_sequence) + 1n;
  for (const item of items) {
    if (BigInt(item.created_sequence) >= previousSequence) throw invalid();
    previousSequence = BigInt(item.created_sequence);
  }
  if (expected.previous?.next_cursor) {
    const before = decodePhase12Cursor(expected.previous.next_cursor, projectId, "reports") as ReportsCursor;
    if (items.some((item) => BigInt(item.created_sequence) >= BigInt(before.last))) throw invalid();
  }
  if (page.next_cursor !== null) {
    const cursor = decodePhase12Cursor(page.next_cursor, projectId, "reports") as ReportsCursor;
    if (cursor.stream_id !== page.stream_id || cursor.upper !== page.as_of_sequence
      || cursor.dismissal !== page.dismissal || cursor.work_item_id !== page.work_item_id
      || cursor.last !== items.at(-1)?.created_sequence) throw invalid();
  }
  return { ...page, items } as unknown as JobReportPage;
}
export function decodeReportCount(value: unknown, projectId: string): JobReportCount {
  const count = objectValue(value);
  if (!count || !exactKeys(count, REPORT_COUNT_FIELDS)
    || !sameUuid(count.project_id, projectId) || !decimalString(count.undismissed_count)
    || !decimalString(count.as_of_sequence)) throw invalid();
  return count as unknown as JobReportCount;
}
function decodeReportFollowUpValue(
  value: unknown,
  projectId: string | undefined,
  reportId?: string
): JobReportFollowUp {
  const link = objectValue(value);
  if (!link || !exactKeys(link, REPORT_FOLLOW_UP_FIELDS)
    || !validUuid(link.id) || !validUuid(link.project_id)
    || projectId !== undefined && !sameUuid(link.project_id, projectId)
    || !validUuid(link.report_id)
    || reportId !== undefined && !sameUuid(link.report_id, reportId)
    || !decimalString(link.created_sequence, true) || !validUuid(link.source_work_item_id) || !validUuid(link.follow_up_work_item_id)
    || sameUuid(link.source_work_item_id, link.follow_up_work_item_id)
    || !boundedText(link.actor_client, 80) || !boundedText(link.actor_session_id, 200)
    || !nullableBoundedText(link.actor_model, 120) || !validUtcDateTime(link.created_at)) throw invalid();
  return link as unknown as JobReportFollowUp;
}
export function decodeReportFollowUp(
  value: unknown,
  projectId: string,
  reportId?: string
): JobReportFollowUp {
  return decodeReportFollowUpValue(value, projectId, reportId);
}
export function decodeReportProvenancePage(value: unknown, projectId: string, focal: {
  reportId?: string; workItemId?: string; direction?: "origin" | "created";
}): JobReportProvenancePage {
  const page = objectValue(value);
  if (!page || !exactKeys(page, ["project_id", "items", "as_of_sequence", "has_more", "next_cursor",
    ...(focal.reportId ? ["report_id"] : ["work_item_id", "direction"])])
    || !sameUuid(page.project_id, projectId) || !decimalString(page.as_of_sequence)
    || !Array.isArray(page.items) || page.items.length > 50 || typeof page.has_more !== "boolean"
    || (page.has_more ? !validOpaqueCursor(page.next_cursor) || page.items.length === 0 : page.next_cursor !== null)
    || focal.reportId && !sameUuid(page.report_id, focal.reportId)
    || focal.workItemId && (!sameUuid(page.work_item_id, focal.workItemId) || page.direction !== focal.direction)) throw invalid();
  const globalWorkHistory = focal.workItemId !== undefined;
  const items = page.items.map((item) => decodeReportFollowUpValue(
    item,
    globalWorkHistory ? undefined : projectId,
    focal.reportId
  ));
  if (new Set(items.map((item) => item.id)).size !== items.length || items.some((item) => focal.workItemId
    && !sameUuid(focal.direction === "origin" ? item.follow_up_work_item_id : item.source_work_item_id, focal.workItemId))) throw invalid();
  if (globalWorkHistory) {
    for (let index = 1; index < items.length; index += 1) {
      const previous = items[index - 1]!;
      const current = items[index]!;
      const timeOrder = compareUtcDateTimes(previous.created_at, current.created_at);
      if (
        timeOrder > 0
        || timeOrder === 0
          && previous.id.toLowerCase() >= current.id.toLowerCase()
      ) throw invalid();
    }
  } else {
    let previousSequence = 0n;
    for (const item of items) {
      if (
        BigInt(item.created_sequence) <= previousSequence
        || BigInt(item.created_sequence) > BigInt(page.as_of_sequence)
      ) throw invalid();
      previousSequence = BigInt(item.created_sequence);
    }
  }
  if (page.next_cursor !== null) {
    if (!globalWorkHistory) {
      const cursor = decodePhase12Cursor(
        page.next_cursor,
        projectId,
        "report_follow_ups"
      ) as ProvenanceCursor;
      if (
        cursor.upper !== page.as_of_sequence
        || cursor.last !== items.at(-1)?.created_sequence
        || !sameUuid(cursor.report_id, focal.reportId)
      ) throw invalid();
    }
  }
  return { ...page, items } as unknown as JobReportProvenancePage;
}
export interface JobReportDraft { summary: string; fyiItems: string[]; promptRevision: string | null; }
export const emptyJobReportDraft = (): JobReportDraft => ({ summary: "", fyiItems: [], promptRevision: null });
export function jobReportDraftHasEdits(draft: JobReportDraft): boolean {
  return Boolean(draft.summary.trim()) || draft.fyiItems.some((item) => Boolean(item.trim()));
}
export function jobReportFromDraft(draft: JobReportDraft): JobCompletionReportInput {
  const value = { summary: draft.summary, fyi_items: draft.fyiItems, prompt_revision: draft.promptRevision };
  if (!validJobReportInput(value)) throw new Error("Write one nonblank summary paragraph and up to ten short FYI bullets. Review the project prompt before submitting; each field must fit its displayed limits.");
  return value;
}
