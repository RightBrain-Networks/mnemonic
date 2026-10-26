import { decodeKnowledgePage, type KnowledgePage } from "./knowledge.ts";
import { decodeReadiness } from "./readiness-codecs.ts";
import type { Readiness, WorkStatus } from "./types.ts";
import { boundedText, exactKeys, finiteInteger, objectValue, validUtcDateTime, validUuid } from "./wire-guards.ts";

type WorkPointer = { work_item_id: string; project_id: string };
export type LineageWork = WorkPointer & {
  title: string; status: WorkStatus; deleted_at: string | null; canonical_work_item_id: string;
  readiness: Readiness | null; is_decision_source: boolean;
  discovered_from: { items: WorkPointer[]; total: number; omitted_count: number };
  code_review: { review_id: string; state: string; status: string | null } | null;
  review_follow_up: { follow_up_id: string; state: string; status: string | null } | null;
  remediation_origin: { review_id: string; source_work_item_id: string; source_project_id: string; depth: number } | null;
};
export type LineageWorkPage = KnowledgePage<LineageWork>;
const workStatuses = ["pending", "deferred", "done", "wont-do", "promoted"];
const reviewStatuses = ["to-review", "deferred", "done", "wont-do", "promoted"];
function invalid(): never { throw new Error("Mnemonic returned invalid work in this decision change."); }
function validReview(value: unknown, field: "review_id" | "follow_up_id"): boolean {
  if (value === null) return true;
  const row = objectValue(value);
  return Boolean(row && exactKeys(row, [field, "state", "status"]) && validUuid(row[field])
    && (field === "review_id" ? ["requested", "completed", "superseded"] : ["pending", "answered", "superseded"]).includes(String(row.state))
    && (row.status === null || reviewStatuses.includes(String(row.status))));
}
function decodeLineageWork(value: unknown): LineageWork {
  const row = objectValue(value), origins = objectValue(row?.discovered_from);
  if (!row || !exactKeys(row, ["work_item_id", "project_id", "title", "status", "deleted_at", "canonical_work_item_id",
    "readiness", "is_decision_source", "discovered_from", "code_review", "review_follow_up", "remediation_origin"])
    || !validUuid(row.work_item_id) || !validUuid(row.project_id) || !validUuid(row.canonical_work_item_id)
    || !boundedText(row.title, 500) || !workStatuses.includes(String(row.status))
    || !(row.deleted_at === null || validUtcDateTime(row.deleted_at)) || typeof row.is_decision_source !== "boolean"
    || !origins || !exactKeys(origins, ["items", "total", "omitted_count"])
    || !Array.isArray(origins.items) || origins.items.length > 20
    || !finiteInteger(origins.total) || !finiteInteger(origins.omitted_count)
    || origins.total !== origins.items.length + origins.omitted_count
    || !origins.items.every(item => { const origin = objectValue(item); return origin
      && exactKeys(origin, ["work_item_id", "project_id"]) && validUuid(origin.work_item_id) && validUuid(origin.project_id); })
    || new Set(origins.items.map(item => (item as WorkPointer).work_item_id)).size !== origins.items.length
    || !validReview(row.code_review, "review_id") || !validReview(row.review_follow_up, "follow_up_id")) invalid();
  if (row.remediation_origin !== null) {
    const origin = objectValue(row.remediation_origin);
    if (!origin || !exactKeys(origin, ["review_id", "source_work_item_id", "source_project_id", "depth"])
      || !validUuid(origin.review_id) || !validUuid(origin.source_work_item_id)
      || !validUuid(origin.source_project_id) || !finiteInteger(origin.depth, 1, 2)) invalid();
  }
  const readiness = row.deleted_at === null
    ? decodeReadiness(row.readiness, row.status as WorkStatus, row.work_item_id as string) : null;
  if (row.deleted_at !== null && row.readiness !== null
    || readiness && readiness.canonical_work_item_id !== row.canonical_work_item_id) invalid();
  return { ...row, readiness } as LineageWork;
}
export function decodeLineageWorkPage(value: unknown, reviewWorkId: string | null): LineageWorkPage {
  const row = objectValue(value);
  if (!row || !exactKeys(row, ["items", "total", "limit", "offset", "next_offset"])) invalid();
  const page = decodeKnowledgePage(value, decodeLineageWork);
  const following = page.offset + page.items.length;
  if (page.limit > 100 || page.items.length !== Math.min(page.limit, Math.max(0, page.total - page.offset))
    || page.next_offset !== (following < page.total ? following : null)
    || new Set(page.items.map(item => item.work_item_id)).size !== page.items.length
    || page.items.some(item => item.work_item_id === reviewWorkId)) invalid();
  return page;
}
