import { validSparseReferences, referenceKeys } from "./external-references.ts";
import type { WorkIdentityPointer, WorkItem, WorkStatus, WorkSummary } from "./types.ts";
import { decodeCheckpointPointer } from "./checkpoint-codecs.ts";
import { decodeReadiness } from "./readiness-codecs.ts";
import {
  boundedText,
  exactKeys,
  finiteInteger,
  objectValue,
  sameUuid,
  validUtcDateTime,
  validUuid
} from "./wire-guards.ts";

const WORK_ITEM_FIELDS = [
  "id", "project_id", "title", "summary", "status", "priority", "initial_checkpoint_id",
  "version", "created_at", "updated_at", "external_references"
] as const;

const WORK_STATUSES = new Set<WorkStatus>([
  "pending", "deferred", "done", "wont-do", "promoted"
]);

const ANCESTOR_FIELDS = ["id", "title", "status"] as const;
const WORK_SUMMARY_FIELDS = [
  "work_item", "checkpoint_count", "ancestor_path", "ancestor_path_truncated",
  "current_context", "readiness"
] as const;

export const WORK_DECODER_FIELDS = {
  decodeWorkItem: WORK_ITEM_FIELDS,
  decodeWorkIdentityPointer: ANCESTOR_FIELDS,
  decodeWorkSummary: WORK_SUMMARY_FIELDS
} as const;

export function decodeWorkItem(
  value: unknown,
  projectId: string,
  workItemId?: string,
  errorMessage = "Mnemonic returned an invalid mutation response."
): WorkItem {
  const item = objectValue(value);
  if (
    !item
    || !validSparseReferences(item)
    || !exactKeys(item, referenceKeys(item, WORK_ITEM_FIELDS.filter((key) => key !== "external_references")))
    || !validUuid(item.id)
    || !sameUuid(item.project_id, projectId)
    || (workItemId !== undefined && !sameUuid(item.id, workItemId))
    || !boundedText(item.title, 200)
    || !boundedText(item.summary, 1_000)
    || typeof item.status !== "string"
    || !WORK_STATUSES.has(item.status as WorkStatus)
    || !finiteInteger(item.priority, 0, 100)
    || !validUuid(item.initial_checkpoint_id)
    || !finiteInteger(item.version, 1)
    || !validUtcDateTime(item.created_at)
    || !validUtcDateTime(item.updated_at)
  ) throw new Error(errorMessage);
  return item as unknown as WorkItem;
}

export function decodeWorkIdentityPointer(value: unknown): WorkIdentityPointer {
  const ancestor = objectValue(value);
  if (
    !ancestor
    || !exactKeys(ancestor, ANCESTOR_FIELDS)
    || !validUuid(ancestor.id)
    || !boundedText(ancestor.title, 200)
    || typeof ancestor.status !== "string"
    || !WORK_STATUSES.has(ancestor.status as WorkStatus)
  ) throw new Error("Mnemonic returned an invalid attention ancestry path.");
  return ancestor as unknown as WorkIdentityPointer;
}

export function decodeWorkSummary(value: unknown, projectId: string): WorkSummary {
  const summary = objectValue(value);
  if (
    !summary
    || !exactKeys(summary, WORK_SUMMARY_FIELDS)
  ) throw new Error("Mnemonic returned an invalid attention work summary.");
  const workItem = decodeWorkItem(
    summary.work_item,
    projectId,
    undefined,
    "Mnemonic returned an invalid attention work summary."
  );
  if (
    !finiteInteger(summary.checkpoint_count, 1)
    || !Array.isArray(summary.ancestor_path)
    || summary.ancestor_path.length > 50
    || typeof summary.ancestor_path_truncated !== "boolean"
  ) throw new Error("Mnemonic returned an invalid attention work summary.");
  const ancestors = summary.ancestor_path.map(decodeWorkIdentityPointer);
  if (new Set(ancestors.map((item) => item.id.toLowerCase())).size !== ancestors.length) {
    throw new Error("Mnemonic returned an invalid attention ancestry path.");
  }
  return {
    work_item: workItem,
    checkpoint_count: summary.checkpoint_count,
    ancestor_path: ancestors,
    ancestor_path_truncated: summary.ancestor_path_truncated,
    current_context: decodeCheckpointPointer(summary.current_context, workItem.id),
    readiness: decodeReadiness(summary.readiness, workItem.status, workItem.id)
  };
}
