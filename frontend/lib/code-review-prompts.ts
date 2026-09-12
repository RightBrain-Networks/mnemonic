import {
  decodeReviewScope,
  type CodeReviewScope,
} from "./code-reviews.ts";
import { validUuid } from "./wire-guards.ts";

export interface ColdReviewPointer {
  project_id: string;
  work_item_id: string;
  code_review_id: string;
  review_version: number;
  scope_sha256: string;
  scope: CodeReviewScope;
}

export function validateColdReviewPointer(pointer: ColdReviewPointer): void {
  if (
    ![pointer.project_id, pointer.work_item_id, pointer.code_review_id].every(
      validUuid,
    ) ||
    !Number.isSafeInteger(pointer.review_version) ||
    pointer.review_version < 1 ||
    !/^[a-f0-9]{64}$/.test(pointer.scope_sha256)
  )
    throw new Error("Review routing is unavailable. Refresh the work item.");
  decodeReviewScope(pointer.scope);
}
