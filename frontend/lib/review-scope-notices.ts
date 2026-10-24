import { exactKeys, finiteInteger, objectValue, sameUuid, validUuid } from "./wire-guards.ts";

export type ReviewScopeChanged = {
  change_id: string; project_id: string; review_work_item_id: string;
  old_scope_version: number; new_scope_version: number; episodes_added: number;
  captured_code_impact_rows_added: number | null;
  distinct_references_in_added_rows: number | null;
  distinct_decisions_in_added_rows: number | null;
};
const FIELDS = ["change_id", "project_id", "review_work_item_id", "old_scope_version", "new_scope_version", "episodes_added", "captured_code_impact_rows_added", "distinct_references_in_added_rows", "distinct_decisions_in_added_rows"] as const;

export function validReviewScopeNotice(value: unknown, workItemId: string): value is ReviewScopeChanged {
  const notice = objectValue(value);
  if (!notice || !exactKeys(notice, FIELDS) || !validUuid(notice.change_id)
    || !validUuid(notice.project_id) || !sameUuid(notice.review_work_item_id, workItemId)
    || !finiteInteger(notice.old_scope_version) || !finiteInteger(notice.new_scope_version, 1)
    || !finiteInteger(notice.episodes_added, 1)
    || notice.new_scope_version - notice.old_scope_version !== notice.episodes_added) return false;
  const counts = [notice.captured_code_impact_rows_added, notice.distinct_references_in_added_rows, notice.distinct_decisions_in_added_rows];
  return counts.every((count) => count === null) || counts.every((count) => finiteInteger(count))
    && Number(counts[0]) >= Number(counts[1]) && Number(counts[1]) >= Number(counts[2]);
}
