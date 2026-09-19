import { decodeLease } from "./readiness-codecs.ts";
import { exactKeys, finiteInteger, objectValue, sameUuid, validUtcDateTime, validUuid } from "./wire-guards.ts";
import type { LeasePublic } from "./types.ts";

export const taskStatusLabels = {
  pending: "Pending", active: "Active", dropped: "Dropped", deferred: "Deferred",
  done: "Done", "wont-do": "Won’t do", promoted: "Promoted", superseded: "Superseded", all: "All"
} as const;
export type TaskStatus = Exclude<keyof typeof taskStatusLabels, "all">;
export type TaskKind = "work_item" | "code_review";
export type Task = {
  id: string; kind: TaskKind; project_id: string; work_item_id: string; work_version: number;
  review_state: "requested" | "completed" | "superseded" | null;
  title: string; summary: string; status: TaskStatus; updated_at: string; lease: LeasePublic | null;
};
export type TaskCounts = { active: number; pending: number };
export type TaskPage = {
  project_id: string; work_items: TaskCounts; code_reviews: TaskCounts;
  next_lease_expires_at: string | null;
  items: Task[]; total: number; limit: number; offset: number;
};
export const TASK_PAGE_SIZE = 20;

export function taskPath(task: Pick<Task, "kind" | "id" | "project_id" | "work_item_id">): string {
  const query = new URLSearchParams({ project: task.project_id, work: task.work_item_id });
  if (task.kind === "code_review") query.set("review", task.id);
  return `/${task.kind === "work_item" ? "work-items" : "code-reviews"}?${query}`;
}

export function decodeTaskPage(value: unknown, projectId: string, status: TaskStatus | "all", kind: TaskKind | undefined, offset: number): TaskPage {
  const page = objectValue(value);
  const counts = (value: unknown) => {
    const row = objectValue(value);
    return row && exactKeys(row, ["active", "pending"]) && finiteInteger(row.active) && finiteInteger(row.pending);
  };
  if (!page || !exactKeys(page, ["project_id", "work_items", "code_reviews", "next_lease_expires_at", "items", "total", "limit", "offset"])
    || !sameUuid(page.project_id, projectId) || !counts(page.work_items) || !counts(page.code_reviews)
    || page.next_lease_expires_at !== null && !validUtcDateTime(page.next_lease_expires_at)
    || !finiteInteger(page.total) || page.limit !== TASK_PAGE_SIZE || page.offset !== offset
    || !Array.isArray(page.items) || page.items.length !== Math.min(TASK_PAGE_SIZE, Math.max(0, page.total - offset))) {
    throw new Error("Mnemonic returned an invalid task page.");
  }
  const seen = new Set<string>();
  const items = page.items.map((value) => {
    const row = objectValue(value);
    if (!row || !exactKeys(row, ["id", "kind", "project_id", "work_item_id", "work_version", "title", "summary", "status", "review_state", "updated_at", "lease"])
      || !finiteInteger(row.work_version, 1) || !validUuid(row.id) || !validUuid(row.work_item_id) || !sameUuid(row.project_id, projectId)
      || !["work_item", "code_review"].includes(String(row.kind)) || kind && row.kind !== kind
      || (row.kind === "work_item" ? row.review_state !== null : !["requested", "completed", "superseded"].includes(String(row.review_state)))
      || typeof row.title !== "string" || !row.title.trim() || typeof row.summary !== "string"
      || !Object.hasOwn(taskStatusLabels, String(row.status)) || row.status === "all"
      || status !== "all" && row.status !== status || !validUtcDateTime(row.updated_at)
      || row.kind === "work_item" && !sameUuid(row.id, row.work_item_id) || seen.has(String(row.id))) {
      throw new Error("Mnemonic returned an invalid task.");
    }
    seen.add(String(row.id));
    const lease = decodeLease(row.lease);
    if ((row.status === "active") !== Boolean(lease)
      || lease && (row.kind === "code_review" ? !sameUuid(lease.code_review_id, row.id) : lease.purpose === "code_review")) {
      throw new Error("Mnemonic returned an invalid task lease.");
    }
    return { ...row, lease } as Task;
  });
  return { ...page, items } as TaskPage;
}
