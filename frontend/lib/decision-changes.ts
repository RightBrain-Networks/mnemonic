import { exactKeys, finiteInteger, objectValue, sameUuid, validUtcDateTime, validUuid } from "./wire-guards.ts";

export type DecisionRevisionChange = {
  change_id: string | null; kind: "substantive" | "record-existing" | "maintenance";
  reason: string | null; governing_reference: string | null; source_work_item_id: string | null;
  requires_admission?: boolean;
};
export type DecisionChange = {
  id: string; project_id: string; origin_reference: string | null;
  review_work_item_id: string | null; parent_change_id: string | null;
  review_work_project_id: string | null; review_closed: boolean; successor_change_id: string | null;
  scope_version: number; acknowledged_scope_version: number;
  assessment_count: number; revision_count: number; pending_revision_count: number; created_at: string; updated_at: string;
};
const reference = /^[A-Z]{1,5}-[0-9]{5,19}-[0-9]{1,10}$/;
const nullableUuid = (value: unknown) => value === null || validUuid(value);
const nullableReference = (value: unknown) => value === null || typeof value === "string" && reference.test(value);

export function decodeDecisionRevisionChange(value: unknown): DecisionRevisionChange {
  const row = objectValue(value);
  if (!row || !exactKeys(row, ["change_id", "kind", "reason", "governing_reference", "source_work_item_id", ...(Object.hasOwn(row, "requires_admission") ? ["requires_admission"] : [])])
    || row.requires_admission !== undefined && typeof row.requires_admission !== "boolean"
    || !nullableUuid(row.change_id) || !nullableUuid(row.source_work_item_id)
    || !["substantive", "record-existing", "maintenance"].includes(String(row.kind))
    || !(row.reason === null || typeof row.reason === "string" && row.reason.trim() && row.reason.length <= 8000 && !row.reason.includes("\0"))
    || !nullableReference(row.governing_reference)
    || row.kind !== "record-existing" && row.governing_reference !== null
    || row.kind !== "substantive" && row.reason === null
    || row.kind === "record-existing" && (!row.change_id || !row.governing_reference)) {
    throw new Error("Mnemonic returned an invalid decision change classification.");
  }
  return row as DecisionRevisionChange;
}

export function decodeDecisionChange(value: unknown, projectId: string, changeId?: string): DecisionChange {
  const row = objectValue(value);
  if (!row || !exactKeys(row, ["id", "project_id", "origin_reference", "review_work_item_id", "parent_change_id",
    "review_work_project_id", "review_closed", "successor_change_id",
    "scope_version", "acknowledged_scope_version", "assessment_count", "revision_count", "pending_revision_count", "created_at", "updated_at"])
    || !validUuid(row.id) || !sameUuid(row.project_id, projectId) || changeId !== undefined && !sameUuid(row.id, changeId)
    || !nullableReference(row.origin_reference) || !nullableUuid(row.review_work_item_id) || !nullableUuid(row.parent_change_id)
    || !nullableUuid(row.review_work_project_id) || typeof row.review_closed !== "boolean" || !nullableUuid(row.successor_change_id)
    || !finiteInteger(row.scope_version) || !finiteInteger(row.acknowledged_scope_version, 0, Number(row.scope_version))
    || !finiteInteger(row.assessment_count) || row.assessment_count !== row.scope_version
    || !finiteInteger(row.revision_count) || !finiteInteger(row.pending_revision_count)
    || !validUtcDateTime(row.created_at) || !validUtcDateTime(row.updated_at)) {
    throw new Error("Mnemonic returned invalid decision review scope.");
  }
  return row as DecisionChange;
}

export function matchDecisionChangeMutation(value: unknown, projectId: string, body: Record<string, unknown>): DecisionChange {
  const row = decodeDecisionChange(value, projectId);
  const matches = body.action === "adopt"
    ? sameUuid(row.review_work_item_id, body.work_item_id) && finiteInteger(body.expected_work_version, 1)
      && Array.isArray(body.assessment_ids) && body.assessment_ids.length > 0 && body.assessment_ids.every(validUuid)
      && row.assessment_count >= body.assessment_ids.length
    : body.action === "acknowledge"
      ? sameUuid(row.id, body.change_id) && row.scope_version === body.expected_scope_version
        && row.acknowledged_scope_version === body.expected_scope_version
      : body.action === "admit" && sameUuid(row.parent_change_id, body.change_id)
        && !sameUuid(row.id, body.change_id) && finiteInteger(body.expected_scope_version);
  if (!matches) throw new Error("Mnemonic returned a decision change that does not match this action.");
  return row;
}
