import type { Checkpoint, CheckpointPointer, Page } from "./types.ts";
import { validAffectedPaths } from "./affected-paths.ts";
import {
  boundedText,
  exactKeys,
  finiteInteger,
  jsonEqual,
  nullableBoundedText,
  nullableUuid,
  objectValue,
  sameUuid,
  validJson,
  validUtcDateTime,
  validUuid
} from "./wire-guards.ts";

const CHECKPOINT_KINDS = new Set(["context", "progress", "completion"]);
const CHECKPOINT_POINTER_FIELDS = [
  "id", "work_item_id", "kind", "source_client", "source_session_id", "source_model",
  "repository_branch", "verified_against", "tags", "migration_origin", "legacy_record_id",
  "created_at"
] as const;

const CHECKPOINT_RESPONSE_FIELDS = [
  "id", "work_item_id", "kind", "prompt", "source_client", "source_session_id",
  "source_model", "source_session_url", "repository_branch", "verified_against", "tags",
  "affected_paths", "source_metadata", "migration_origin", "legacy_record_id", "created_at"
] as const;
const CHECKPOINT_REQUIRED_RESPONSE_FIELDS = CHECKPOINT_RESPONSE_FIELDS.filter(
  (field) => field !== "affected_paths"
);

export const CHECKPOINT_DECODER_FIELDS = {
  decodeCheckpoint: CHECKPOINT_RESPONSE_FIELDS,
  decodeCheckpointPointer: CHECKPOINT_POINTER_FIELDS
} as const;

export function decodeCheckpointPointer(
  value: unknown,
  workItemId: string,
  errorMessage = "Mnemonic returned an invalid attention checkpoint pointer."
): CheckpointPointer {
  const checkpoint = objectValue(value);
  if (
    !checkpoint
    || !exactKeys(checkpoint, CHECKPOINT_POINTER_FIELDS)
    || !validUuid(checkpoint.id)
    || !sameUuid(checkpoint.work_item_id, workItemId)
    || !["context", "progress", "completion"].includes(String(checkpoint.kind))
    || !boundedText(checkpoint.source_client, 80)
    || !boundedText(checkpoint.source_session_id, 200)
    || !nullableBoundedText(checkpoint.source_model, 120)
    || !nullableBoundedText(checkpoint.repository_branch, 200)
    || !(checkpoint.verified_against === null
      || typeof checkpoint.verified_against === "string"
        && /^[a-fA-F0-9]{7,64}$/.test(checkpoint.verified_against))
    || !Array.isArray(checkpoint.tags)
    || checkpoint.tags.some((tag) => !boundedText(tag, 50))
    || !(checkpoint.migration_origin === null
      || checkpoint.migration_origin === "legacy-handoff-snapshot"
      || checkpoint.migration_origin === "legacy-comment")
    || !(checkpoint.legacy_record_id === null || validUuid(checkpoint.legacy_record_id))
    || !validUtcDateTime(checkpoint.created_at)
  ) throw new Error(errorMessage);
  return checkpoint as unknown as CheckpointPointer;
}

export function decodeCheckpoint(
  value: unknown,
  workItemId: string,
  expectedKind?: string,
  expectedInput?: unknown
): Checkpoint {
  const checkpoint = objectValue(value);
  const hasAffectedPaths = checkpoint !== null
    && Object.hasOwn(checkpoint, "affected_paths");
  if (
    !checkpoint
    || !exactKeys(
      checkpoint,
      hasAffectedPaths ? CHECKPOINT_RESPONSE_FIELDS : CHECKPOINT_REQUIRED_RESPONSE_FIELDS
    )
    || !validUuid(checkpoint.id)
    || !sameUuid(checkpoint.work_item_id, workItemId)
    || typeof checkpoint.kind !== "string"
    || !CHECKPOINT_KINDS.has(checkpoint.kind)
    || (expectedKind !== undefined && checkpoint.kind !== expectedKind)
    || !boundedText(checkpoint.prompt, 100_000)
    || !boundedText(checkpoint.source_client, 80)
    || !boundedText(checkpoint.source_session_id, 200)
    || !nullableBoundedText(checkpoint.source_model, 120)
    || !(checkpoint.source_session_url === null || boundedText(checkpoint.source_session_url, 2_000))
    || !nullableBoundedText(checkpoint.repository_branch, 200)
    || !(checkpoint.verified_against === null
      || (typeof checkpoint.verified_against === "string" && /^[a-fA-F0-9]{7,64}$/.test(checkpoint.verified_against)))
    || hasAffectedPaths && (
      !validAffectedPaths(checkpoint.affected_paths)
      || checkpoint.affected_paths.length === 0
      || checkpoint.verified_against === null
    )
    || !Array.isArray(checkpoint.tags)
    || checkpoint.tags.some((tag) => !boundedText(tag, 50) || tag !== tag.toLowerCase())
    || new Set(checkpoint.tags).size !== checkpoint.tags.length
    || !objectValue(checkpoint.source_metadata)
    || !validJson(checkpoint.source_metadata)
    || !(checkpoint.migration_origin === null
      || checkpoint.migration_origin === "legacy-handoff-snapshot"
      || checkpoint.migration_origin === "legacy-comment")
    || !nullableUuid(checkpoint.legacy_record_id)
    || !validUtcDateTime(checkpoint.created_at)
  ) {
    throw new Error("Mnemonic returned an invalid mutation response.");
  }
  if (expectedInput !== undefined) {
    const input = objectValue(expectedInput);
    const expectedAffectedPaths = input?.affected_paths ?? [];
    const expectedVerified = input?.verified_against === undefined
      || input.verified_against === null
      ? null
      : typeof input.verified_against === "string"
        ? input.verified_against.trim().toLowerCase()
        : undefined;
    if (
      !input
      || expectedVerified === undefined
      || !validAffectedPaths(expectedAffectedPaths)
      || checkpoint.migration_origin !== null
      || checkpoint.legacy_record_id !== null
      || checkpoint.prompt !== input.prompt
      || checkpoint.source_client !== input.source_client
      || checkpoint.source_session_id !== input.source_session_id
      || checkpoint.source_model !== (input.source_model ?? null)
      || checkpoint.source_session_url !== (input.source_session_url ?? null)
      || checkpoint.repository_branch !== (input.repository_branch ?? null)
      || checkpoint.verified_against !== expectedVerified
      || !jsonEqual(
        hasAffectedPaths ? checkpoint.affected_paths : [],
        expectedAffectedPaths
      )
      || !jsonEqual(checkpoint.tags, input.tags ?? [])
      || !jsonEqual(checkpoint.source_metadata, input.source_metadata ?? {})
    ) {
      throw new Error("Mnemonic returned an incoherent mutation response.");
    }
  }
  return {
    ...checkpoint,
    affected_paths: hasAffectedPaths ? checkpoint.affected_paths : []
  } as unknown as Checkpoint;
}

export function decodeCheckpointPage(
  value: unknown,
  workItemId: string,
  expected: { limit?: number; offset?: number } = {}
): Page<Checkpoint> {
  const page = objectValue(value);
  if (
    !page
    || !exactKeys(page, ["items", "total", "limit", "offset"])
    || !Array.isArray(page.items)
    || !finiteInteger(page.total)
    || !finiteInteger(page.limit, 1, 100)
    || !finiteInteger(page.offset)
    || page.items.length > page.limit
    || page.items.length > page.total
    || page.items.length > 0 && page.offset + page.items.length > page.total
    || expected.limit !== undefined && page.limit !== expected.limit
    || expected.offset !== undefined && page.offset !== expected.offset
  ) throw new Error("Mnemonic returned an invalid checkpoint page.");
  const items = page.items.map((entry) => decodeCheckpoint(entry, workItemId));
  if (new Set(items.map((checkpoint) => checkpoint.id.toLowerCase())).size !== items.length) {
    throw new Error("Mnemonic returned repeated checkpoint identities.");
  }
  return { items, total: page.total, limit: page.limit, offset: page.offset };
}
