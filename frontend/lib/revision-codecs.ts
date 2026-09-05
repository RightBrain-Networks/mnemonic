import type { HumanGateContextRevision, MergeReviewRevision } from "./types.ts";
import { exactKeys, finiteInteger, objectValue, sameUuid, validUuid } from "./wire-guards.ts";

const HUMAN_GATE_REVISION_FIELDS = [
  "work_version", "context_checkpoint_id", "relationship_event_count"
] as const;
const MERGE_REVIEW_REVISION_FIELDS = [
  "work_version", "context_checkpoint_id", "work_event_count"
] as const;

export const REVISION_DECODER_FIELDS = {
  decodeHumanGateRevision: HUMAN_GATE_REVISION_FIELDS,
  decodeMergeReviewRevision: MERGE_REVIEW_REVISION_FIELDS
} as const;

export function validHumanGateRevision(value: unknown): value is HumanGateContextRevision {
  const revision = objectValue(value);
  return Boolean(
    revision
    && exactKeys(revision, HUMAN_GATE_REVISION_FIELDS)
    && finiteInteger(revision.work_version, 1)
    && validUuid(revision.context_checkpoint_id)
    && finiteInteger(revision.relationship_event_count)
  );
}

export function decodeHumanGateRevision(
  value: unknown,
  errorMessage = "Mnemonic returned an invalid human-gate revision."
): HumanGateContextRevision {
  if (!validHumanGateRevision(value)) throw new Error(errorMessage);
  return value;
}

export function sameHumanGateRevision(
  left: HumanGateContextRevision,
  right: HumanGateContextRevision
): boolean {
  return left.work_version === right.work_version
    && left.relationship_event_count === right.relationship_event_count
    && sameUuid(left.context_checkpoint_id, right.context_checkpoint_id);
}

export function validMergeReviewRevision(value: unknown): value is MergeReviewRevision {
  const revision = objectValue(value);
  return Boolean(
    revision
    && exactKeys(revision, MERGE_REVIEW_REVISION_FIELDS)
    && finiteInteger(revision.work_version, 1)
    && validUuid(revision.context_checkpoint_id)
    && finiteInteger(revision.work_event_count, 1)
  );
}

export function decodeMergeReviewRevision(
  value: unknown,
  errorMessage = "Mnemonic returned an invalid merge review revision."
): MergeReviewRevision {
  if (!validMergeReviewRevision(value)) throw new Error(errorMessage);
  return value;
}

export function sameMergeReviewRevision(
  left: MergeReviewRevision,
  right: MergeReviewRevision
): boolean {
  return left.work_version === right.work_version
    && left.work_event_count === right.work_event_count
    && sameUuid(left.context_checkpoint_id, right.context_checkpoint_id);
}
