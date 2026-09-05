import type { RelationshipEdgeRead } from "./types.ts";
import {
  boundedText,
  exactKeys,
  nullableBoundedText,
  nullableUuid,
  objectValue,
  sameNullableUuid,
  sameUuid,
  validUtcDateTime,
  validUuid,
  type JsonObject
} from "./wire-guards.ts";

const RELATIONSHIP_TYPES = new Set([
  "blocks",
  "parent-child",
  "discovered-from",
  "duplicate-of",
  "related"
]);

const RELATIONSHIP_RESPONSE_FIELDS = [
  "id", "project_id", "relationship_type", "source_work_item_id",
  "target_work_item_id", "context_checkpoint_work_item_id", "context_checkpoint_id",
  "created_by_client", "created_by_session_id", "created_by_model", "created_at"
] as const;

export const RELATIONSHIP_DECODER_FIELDS = {
  decodeRelationship: RELATIONSHIP_RESPONSE_FIELDS
} as const;

type ExpectedRelationship = {
  type: string;
  source: string;
  target: string;
};

export function expectedRelationship(
  input: JsonObject,
  newWorkItemId?: string
): ExpectedRelationship | null {
  const type = input.relationship_type ?? input.type;
  let source = input.source_work_item_id;
  let target = input.target_work_item_id;
  if (newWorkItemId) {
    if (input.direction === "outgoing") {
      source = newWorkItemId;
      target = input.other_work_item_id;
    } else if (input.direction === "incoming") {
      source = input.other_work_item_id;
      target = newWorkItemId;
    } else {
      return null;
    }
  }
  if (
    typeof type !== "string"
    || !RELATIONSHIP_TYPES.has(type)
    || !validUuid(source)
    || !validUuid(target)
    || sameUuid(source, target)
  ) return null;
  let normalizedSource = source;
  let normalizedTarget = target;
  if (
    type === "related"
    && normalizedTarget.toLowerCase() < normalizedSource.toLowerCase()
  ) {
    [normalizedSource, normalizedTarget] = [normalizedTarget, normalizedSource];
  }
  return { type, source: normalizedSource, target: normalizedTarget };
}

export function relationshipIdentity(
  type: unknown,
  source: unknown,
  target: unknown
): string | null {
  return typeof type === "string" && validUuid(source) && validUuid(target)
    ? [type, source.toLowerCase(), target.toLowerCase()].join(":")
    : null;
}

export function decodeRelationship(
  value: unknown,
  projectId: string,
  input?: JsonObject,
  newWorkItemId?: string,
  requestDetailsMustMatch = true
): RelationshipEdgeRead {
  const relationship = objectValue(value);
  if (
    !relationship
    || !exactKeys(relationship, RELATIONSHIP_RESPONSE_FIELDS)
    || !validUuid(relationship.id)
    || !sameUuid(relationship.project_id, projectId)
    || typeof relationship.relationship_type !== "string"
    || !RELATIONSHIP_TYPES.has(relationship.relationship_type)
    || !validUuid(relationship.source_work_item_id)
    || !validUuid(relationship.target_work_item_id)
    || sameUuid(relationship.source_work_item_id, relationship.target_work_item_id)
    || !nullableUuid(relationship.context_checkpoint_work_item_id)
    || !nullableUuid(relationship.context_checkpoint_id)
    || ((relationship.context_checkpoint_id === null) !== (relationship.context_checkpoint_work_item_id === null))
    || (relationship.context_checkpoint_work_item_id !== null
      && !sameUuid(relationship.context_checkpoint_work_item_id, relationship.source_work_item_id)
      && !sameUuid(relationship.context_checkpoint_work_item_id, relationship.target_work_item_id))
    || (relationship.relationship_type === "discovered-from"
      && !sameUuid(relationship.context_checkpoint_work_item_id, relationship.target_work_item_id))
    || !boundedText(relationship.created_by_client, 80)
    || !boundedText(relationship.created_by_session_id, 200)
    || !nullableBoundedText(relationship.created_by_model, 120)
    || !validUtcDateTime(relationship.created_at)
  ) {
    throw new Error("Mnemonic returned an invalid mutation response.");
  }
  if (input) {
    const expected = expectedRelationship(input, newWorkItemId);
    if (!expected) throw new Error("The frozen mutation request is invalid.");
    if (
      relationship.relationship_type !== expected.type
      || !sameUuid(relationship.source_work_item_id, expected.source)
      || !sameUuid(relationship.target_work_item_id, expected.target)
      || requestDetailsMustMatch && (
        relationship.created_by_client !== (input.created_by_client ?? "dashboard")
        || relationship.created_by_session_id !== input.created_by_session_id
        || relationship.created_by_model !== (input.created_by_model ?? null)
        || !sameNullableUuid(relationship.context_checkpoint_id, input.context_checkpoint_id ?? null)
      )
    ) {
      throw new Error("Mnemonic returned an incoherent mutation response.");
    }
  }
  return relationship as unknown as RelationshipEdgeRead;
}
