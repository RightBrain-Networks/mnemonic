import { exactKeys, finiteInteger, objectValue, sameUuid, validUtcDateTime, validUuid } from "./wire-guards.ts";
import { validFindingSummary, type FindingSummary } from "./impact-findings.ts";
import { decodeLineageWorkPage, type LineageWorkPage } from "./decision-lineage.ts";

export type DecisionRevisionChange = {
  change_id: string | null; kind: "substantive" | "record-existing" | "maintenance";
  reason: string | null; governing_reference: string | null; source_work_item_id: string | null;
  requires_admission?: boolean;
  unchanged_supersessions?: { target_reference: string; governing_reference: string; reason: string }[];
};
export type DecisionChange = {
  lineage_work?: LineageWorkPage;
  finding_summary?: FindingSummary | null;
  id: string; project_id: string; origin_reference: string | null;
  review_work_item_id: string | null; parent_change_id: string | null;
  review_work_project_id: string | null; review_closed: boolean; successor_change_id: string | null;
  scope_version: number; acknowledged_scope_version: number;
  assessment_count: number; revision_count: number; pending_revision_count: number; created_at: string; updated_at: string;
};
const reference = /^[A-Z]{1,5}-[0-9]{5,19}-[0-9]{1,10}$/;
const nullableUuid = (value: unknown) => value === null || validUuid(value);
const nullableReference = (value: unknown) => value === null || typeof value === "string" && reference.test(value);

function validUnchangedSupersessions(value: unknown): boolean {
  if (!Array.isArray(value) || value.length > 100) return false;
  const targets = new Set<string>();
  return value.every((item) => {
    const row = objectValue(item);
    if (!row || !exactKeys(row, ["target_reference", "governing_reference", "reason"])
      || typeof row.target_reference !== "string" || !reference.test(row.target_reference)
      || typeof row.governing_reference !== "string" || !reference.test(row.governing_reference)
      || typeof row.reason !== "string" || !row.reason.trim() || Array.from(row.reason).length > 8000
      || row.reason.includes("\0")) return false;
    const target = row.target_reference.slice(0, row.target_reference.lastIndexOf("-"));
    if (targets.has(target)) return false;
    targets.add(target);
    return true;
  });
}

export function decodeDecisionRevisionChange(value: unknown): DecisionRevisionChange {
  const row = objectValue(value);
  if (!row || !exactKeys(row, ["change_id", "kind", "reason", "governing_reference", "source_work_item_id",
    ...(Object.hasOwn(row, "requires_admission") ? ["requires_admission"] : []),
    ...(Object.hasOwn(row, "unchanged_supersessions") ? ["unchanged_supersessions"] : [])])
    || row.requires_admission !== undefined && typeof row.requires_admission !== "boolean"
    || Object.hasOwn(row, "unchanged_supersessions") && !validUnchangedSupersessions(row.unchanged_supersessions)
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
    "scope_version", "acknowledged_scope_version", "assessment_count", "revision_count", "pending_revision_count", "created_at", "updated_at",
    ...(Object.hasOwn(row, "finding_summary") ? ["finding_summary"] : []),
    ...(Object.hasOwn(row, "lineage_work") ? ["lineage_work"] : [])])
    || row.finding_summary != null && !validFindingSummary(row.finding_summary)
    || !validUuid(row.id) || !sameUuid(row.project_id, projectId) || changeId !== undefined && !sameUuid(row.id, changeId)
    || !nullableReference(row.origin_reference) || !nullableUuid(row.review_work_item_id) || !nullableUuid(row.parent_change_id)
    || !nullableUuid(row.review_work_project_id) || typeof row.review_closed !== "boolean" || !nullableUuid(row.successor_change_id)
    || !finiteInteger(row.scope_version) || !finiteInteger(row.acknowledged_scope_version, 0, Number(row.scope_version))
    || !finiteInteger(row.assessment_count) || row.assessment_count !== row.scope_version
    || !finiteInteger(row.revision_count) || !finiteInteger(row.pending_revision_count)
    || !validUtcDateTime(row.created_at) || !validUtcDateTime(row.updated_at)) {
    throw new Error("Mnemonic returned invalid decision review scope.");
  }
  return { ...row, ...(Object.hasOwn(row, "lineage_work")
    ? { lineage_work: decodeLineageWorkPage(row.lineage_work, row.review_work_item_id as string | null) } : {}) } as DecisionChange;
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
      // Retain decoding only for a historical receipt retry; the UI cannot create this action.
      : body.action === "admit" && sameUuid(row.parent_change_id, body.change_id)
        && !sameUuid(row.id, body.change_id) && finiteInteger(body.expected_scope_version);
  if (!matches) throw new Error("Mnemonic returned a decision change that does not match this action.");
  return row;
}
