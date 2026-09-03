import { ApiError, detailMessage } from "./api.ts";
import type {
  Checkpoint,
  CompletionResult,
  DeletionResult,
  RelationshipCreationResult,
  RelationshipEdgeRead,
  RelationshipRemovalResult,
  HumanGateRead,
  Page,
  WorkCreation,
  WorkItem,
  WorkMergeResult
} from "@/lib/types";
import { validAffectedPaths } from "./affected-paths.ts";
import {
  decodeHumanGate,
  decodeWorkIdentityPointer,
  sameHumanGateRevision
} from "./human-gates.ts";
import { isDefinitiveProxyError } from "./proxy-policy.ts";
import {
  UUID_PATTERN,
  boundedText,
  compareUtcDateTimes,
  decodeWorkItem,
  exactKeys,
  finiteInteger,
  nullableBoundedText,
  nullableUuid,
  objectValue,
  sameUuid,
  validUnicode,
  validUtcDateTime,
  validUuid,
  type JsonObject
} from "./wire-guards.ts";
import { decodeWorkEventForWork } from "./work-events.ts";
import type { WorkEventRead } from "@/lib/types";

export const MUTATION_KINDS = [
  "create_work",
  "add_checkpoint",
  "append_event",
  "add_relationship",
  "update_work",
  "defer_work",
  "complete_work",
  "delete_work",
  "remove_relationship",
  "resolve_human_input",
  "merge_work"
] as const;

export type MutationKind = typeof MUTATION_KINDS[number];

export interface MutationResultByKind {
  create_work: WorkCreation;
  add_checkpoint: Checkpoint;
  append_event: WorkEventRead;
  add_relationship: RelationshipCreationResult;
  update_work: WorkItem;
  defer_work: WorkItem;
  complete_work: CompletionResult;
  delete_work: DeletionResult;
  remove_relationship: RelationshipRemovalResult;
  resolve_human_input: HumanGateRead;
  merge_work: WorkMergeResult;
}

export interface FrozenMutationRequest {
  readonly kind: MutationKind;
  readonly method: "POST" | "PATCH" | "DELETE";
  readonly path: string;
  readonly body: string;
  readonly operationId: string;
}

export type MutationHttpOutcome<K extends MutationKind = MutationKind> =
  | { readonly type: "success"; readonly value: MutationResultByKind[K] }
  | { readonly type: "rejected"; readonly error: ApiError }
  | { readonly type: "safety_conflict"; readonly error: ApiError }
  | { readonly type: "unresolved"; readonly message: string };

const CHECKPOINT_KINDS = new Set(["context", "progress", "completion"]);
const RELATIONSHIP_TYPES = new Set([
  "blocks",
  "parent-child",
  "discovered-from",
  "duplicate-of",
  "related"
]);
const CHECKPOINT_RESPONSE_FIELDS = [
  "id", "work_item_id", "kind", "prompt", "source_client", "source_session_id",
  "source_model", "source_session_url", "repository_branch", "verified_against", "tags",
  "affected_paths", "source_metadata", "migration_origin", "legacy_record_id", "created_at"
] as const;
const CHECKPOINT_REQUIRED_RESPONSE_FIELDS = CHECKPOINT_RESPONSE_FIELDS.filter(
  (field) => field !== "affected_paths"
);
const RELATIONSHIP_RESPONSE_FIELDS = [
  "id", "project_id", "relationship_type", "source_work_item_id",
  "target_work_item_id", "context_checkpoint_work_item_id", "context_checkpoint_id",
  "created_by_client", "created_by_session_id", "created_by_model", "created_at"
] as const;

export const MUTATION_RESPONSE_DECODER_FIELDS = {
  decodeCheckpoint: CHECKPOINT_RESPONSE_FIELDS,
  decodeRelationship: RELATIONSHIP_RESPONSE_FIELDS
} as const;
const EXPECTED_STATUS: Record<MutationKind, number> = {
  create_work: 201,
  add_checkpoint: 201,
  append_event: 201,
  add_relationship: 200,
  update_work: 200,
  defer_work: 200,
  complete_work: 200,
  delete_work: 200,
  remove_relationship: 200,
  resolve_human_input: 200,
  merge_work: 201
};
const AMBIGUOUS_STATUSES = new Set([408, 425, 429, 502, 504]);
const ERROR_ROOT_KEYS = new Set(["detail"]);
const STRUCTURED_ERROR_KEYS = new Set(["code", "message", "context"]);
const SAFE_CONTEXT_KEYS = new Set([
  "holder_client", "expires_at", "fields", "canonical_work_item_id"
]);
const VALIDATION_KEYS = new Set(["type", "loc", "msg"]);
const VALIDATION_LOCATION_ROOTS = new Set(["body", "query", "path", "header", "cookie"]);
const DEFINITIVE_APPLICATION_ERRORS = new Map<number, ReadonlySet<string>>([
  [404, new Set([
    "project_not_found",
    "work_item_not_found",
    "checkpoint_not_found",
    "relationship_not_found",
    "gate_not_found"
  ])],
  [409, new Set([
    "version_conflict",
    "invalid_status_transition",
    "work_not_pending",
    "work_blocked",
    "lease_held",
    "lease_expired",
    "lease_token_mismatch",
    "relationship_self_edge",
    "parent_already_set",
    "relationship_context_required",
    "relationship_context_invalid",
    "relationship_cycle",
    "active_relationships",
    "work_gated",
    "gate_already_resolved",
    "gate_context_changed",
    "duplicate_merge_required",
    "duplicate_self",
    "work_duplicate",
    "work_already_duplicate",
    "duplicate_destination_not_canonical",
    "duplicate_context_changed",
    "duplicate_source_gate_unresolved",
    "duplicate_structural_relationships",
    "duplicate_depth_exceeded",
    "duplicate_relationship_frozen"
  ])],
  [422, new Set([
    "event_secret_echo", "client_operation_secret_echo", "gate_secret_echo",
    "merge_secret_echo"
  ])],
  [503, new Set(["duplicate_graph_invalid"])]
]);
const DEFINITIVE_API_STRING_ERRORS = new Map<number, ReadonlySet<string>>([
  [401, new Set(["Valid bearer authentication is required"])]
]);

function validJson(value: unknown, stack = new WeakSet<object>()): boolean {
  if (value === null || typeof value === "boolean") return true;
  if (typeof value === "number") return Number.isFinite(value);
  if (typeof value === "string") return validUnicode(value) && !value.includes("\0");
  if (typeof value !== "object" || stack.has(value)) return false;
  stack.add(value);
  const valid = Array.isArray(value)
    ? value.every((entry) => validJson(entry, stack))
    : Boolean(objectValue(value))
      && Object.entries(value).every(([key, entry]) => (
        validUnicode(key) && !key.includes("\0") && validJson(entry, stack)
      ));
  stack.delete(value);
  return valid;
}

function jsonEqual(left: unknown, right: unknown): boolean {
  if (!validJson(left) || !validJson(right)) return false;
  if (left === right) return true;
  if (Array.isArray(left) || Array.isArray(right)) {
    return Array.isArray(left)
      && Array.isArray(right)
      && left.length === right.length
      && left.every((entry, index) => jsonEqual(entry, right[index]));
  }
  const leftObject = objectValue(left);
  const rightObject = objectValue(right);
  if (!leftObject || !rightObject) return false;
  const leftKeys = Object.keys(leftObject).sort();
  const rightKeys = Object.keys(rightObject).sort();
  return leftKeys.length === rightKeys.length
    && leftKeys.every((key, index) => (
      key === rightKeys[index] && jsonEqual(leftObject[key], rightObject[key])
    ));
}

function parsePath(path: string, suffix: string): { projectId: string; workItemId?: string; relationshipId?: string } | null {
  const workMatch = new RegExp(`^/projects/(${UUID_PATTERN.source.slice(1, -1)})/work-items/(${UUID_PATTERN.source.slice(1, -1)})${suffix}$`).exec(path);
  if (workMatch) return { projectId: workMatch[1]!, workItemId: workMatch[2]! };
  const projectMatch = new RegExp(`^/projects/(${UUID_PATTERN.source.slice(1, -1)})${suffix}$`).exec(path);
  if (projectMatch) return { projectId: projectMatch[1]! };
  const relationshipMatch = new RegExp(`^/projects/(${UUID_PATTERN.source.slice(1, -1)})/relationships/(${UUID_PATTERN.source.slice(1, -1)})${suffix}$`).exec(path);
  if (relationshipMatch) {
    return { projectId: relationshipMatch[1]!, relationshipId: relationshipMatch[2]! };
  }
  return null;
}

function parseGateResolutionPath(path: string): {
  projectId: string;
  workItemId: string;
  gateId: string;
} | null {
  const uuid = UUID_PATTERN.source.slice(1, -1);
  const match = new RegExp(
    `^/projects/(${uuid})/work-items/(${uuid})/gates/(${uuid})/resolve$`
  ).exec(path);
  return match ? { projectId: match[1]!, workItemId: match[2]!, gateId: match[3]! } : null;
}

function requestBody(request: FrozenMutationRequest): JsonObject {
  let parsed: unknown;
  try {
    parsed = JSON.parse(request.body);
  } catch {
    throw new Error("The frozen mutation request is invalid.");
  }
  const body = objectValue(parsed);
  if (!body || !sameUuid(body.client_operation_id, request.operationId)) {
    throw new Error("The frozen mutation request is invalid.");
  }
  return body;
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

type ExpectedRelationship = {
  type: string;
  source: string;
  target: string;
};

function expectedRelationship(
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

function relationshipIdentity(
  type: unknown,
  source: unknown,
  target: unknown
): string | null {
  return typeof type === "string" && validUuid(source) && validUuid(target)
    ? [type, source.toLowerCase(), target.toLowerCase()].join(":")
    : null;
}

function initialRelationshipOrder(input: JsonObject): string {
  const context = input.context_checkpoint_id;
  const direction = input.type === "related" ? "outgoing" : input.direction;
  return [
    String(input.type ?? ""),
    String(direction ?? ""),
    validUuid(input.other_work_item_id)
      ? input.other_work_item_id.toLowerCase()
      : String(input.other_work_item_id ?? ""),
    validUuid(context) ? context.toLowerCase() : ""
  ].join("\0");
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

function sameNullableUuid(left: unknown, right: unknown): boolean {
  return left === null && right === null || sameUuid(left, right);
}

function mergeRevision(value: unknown): {
  work_version: number;
  context_checkpoint_id: string;
  work_event_count: number;
} {
  const revision = objectValue(value);
  if (
    !revision
    || !exactKeys(revision, [
      "work_version", "context_checkpoint_id", "work_event_count"
    ])
    || !finiteInteger(revision.work_version, 1)
    || !validUuid(revision.context_checkpoint_id)
    || !finiteInteger(revision.work_event_count, 1)
  ) throw new Error("Mnemonic returned an invalid merge response.");
  return revision as {
    work_version: number;
    context_checkpoint_id: string;
    work_event_count: number;
  };
}

function sameMergeRevision(left: unknown, right: unknown): boolean {
  try {
    const leftRevision = mergeRevision(left);
    const rightRevision = mergeRevision(right);
    return leftRevision.work_version === rightRevision.work_version
      && leftRevision.work_event_count === rightRevision.work_event_count
      && sameUuid(leftRevision.context_checkpoint_id, rightRevision.context_checkpoint_id);
  } catch {
    return false;
  }
}

function decodeMergeResult(
  value: unknown,
  projectId: string,
  sourceWorkItemId: string,
  body: JsonObject
): WorkMergeResult {
  const result = objectValue(value);
  const merge = objectValue(result?.merge);
  if (
    !result
    || !exactKeys(result, [
      "merge", "source_work_item", "destination_work_item", "direct_destination",
      "canonical_work_item", "supporting_relationship_created",
      "supporting_relationship", "relationship_events", "merge_events"
    ])
    || !merge
    || !exactKeys(merge, [
      "id", "merge_sequence", "project_id", "source_work_item_id",
      "destination_work_item_id", "duplicate_relationship_id",
      "reviewed_source_revision", "reviewed_destination_revision",
      "resulting_source_work_version", "resulting_destination_work_version",
      "rationale", "merged_by_client", "merged_by_session_id", "merged_by_model",
      "created_at"
    ])
    || !validUuid(merge.id)
    || !finiteInteger(merge.merge_sequence, 1)
    || !sameUuid(merge.project_id, projectId)
    || !sameUuid(merge.source_work_item_id, sourceWorkItemId)
    || !validUuid(merge.destination_work_item_id)
    || !sameUuid(merge.destination_work_item_id, body.destination_work_item_id)
    || sameUuid(merge.source_work_item_id, merge.destination_work_item_id)
    || !validUuid(merge.duplicate_relationship_id)
    || !sameMergeRevision(merge.reviewed_source_revision, body.reviewed_source_revision)
    || !sameMergeRevision(
      merge.reviewed_destination_revision,
      body.reviewed_destination_revision
    )
    || !finiteInteger(merge.resulting_source_work_version, 2)
    || !finiteInteger(merge.resulting_destination_work_version, 2)
    || merge.resulting_source_work_version
      !== mergeRevision(merge.reviewed_source_revision).work_version + 1
    || merge.resulting_destination_work_version
      !== mergeRevision(merge.reviewed_destination_revision).work_version + 1
    || !boundedText(merge.rationale, 4_000)
    || merge.rationale !== body.rationale
    || merge.merged_by_client !== body.merged_by_client
    || merge.merged_by_session_id !== body.merged_by_session_id
    || merge.merged_by_model !== (body.merged_by_model ?? null)
    || !validUtcDateTime(merge.created_at)
    || typeof result.supporting_relationship_created !== "boolean"
    || !Array.isArray(result.relationship_events)
    || !Array.isArray(result.merge_events)
  ) throw new Error("Mnemonic returned an invalid merge response.");

  const source = decodeWorkItem(result.source_work_item, projectId, sourceWorkItemId);
  const destination = decodeWorkItem(
    result.destination_work_item,
    projectId,
    merge.destination_work_item_id as string
  );
  const directDestination = decodeWorkIdentityPointer(result.direct_destination);
  const canonicalWorkItem = decodeWorkIdentityPointer(result.canonical_work_item);
  if (
    source.version !== merge.resulting_source_work_version
    || destination.version !== merge.resulting_destination_work_version
    || source.updated_at !== merge.created_at
    || destination.updated_at !== merge.created_at
    || !sameUuid(directDestination.id, destination.id)
    || directDestination.title !== destination.title
    || directDestination.status !== destination.status
    || !sameUuid(canonicalWorkItem.id, destination.id)
    || canonicalWorkItem.title !== destination.title
    || canonicalWorkItem.status !== destination.status
  ) throw new Error("Mnemonic returned an incoherent merge response.");

  const relationship = decodeRelationship(result.supporting_relationship, projectId);
  if (
    !sameUuid(relationship.id, merge.duplicate_relationship_id)
    || relationship.relationship_type !== "duplicate-of"
    || !sameUuid(relationship.source_work_item_id, source.id)
    || !sameUuid(relationship.target_work_item_id, destination.id)
    || compareUtcDateTimes(relationship.created_at, merge.created_at as string) > 0
    || result.supporting_relationship_created && (
      relationship.created_by_client !== merge.merged_by_client
      || relationship.created_by_session_id !== merge.merged_by_session_id
      || relationship.created_by_model !== merge.merged_by_model
      || relationship.created_at !== merge.created_at
      || relationship.context_checkpoint_work_item_id !== null
      || relationship.context_checkpoint_id !== null
    )
  ) throw new Error("Mnemonic returned an incoherent merge relationship.");

  const relationshipEvents = result.relationship_events.map((entry, index) => {
    const expectedWork = index === 0 ? source.id : destination.id;
    const event = decodeWorkEventForWork(entry, projectId, expectedWork);
    const metadata = objectValue(event.metadata);
    if (
      event.event_type !== "relationship_added"
      || !sameUuid(event.relationship_id, relationship.id)
      || !sameUuid(event.relationship_source_work_item_id, source.id)
      || !sameUuid(event.relationship_target_work_item_id, destination.id)
      || !sameNullableUuid(
        event.relationship_context_checkpoint_work_item_id,
        relationship.context_checkpoint_work_item_id
      )
      || !sameNullableUuid(
        event.relationship_context_checkpoint_id,
        relationship.context_checkpoint_id
      )
      || event.relationship_direction !== (index === 0 ? "outgoing" : "incoming")
      || !sameUuid(event.counterpart_work_item_id, index === 0 ? destination.id : source.id)
      || metadata?.relationship_type !== "duplicate-of"
      || event.origin !== "live"
      || event.actor_kind !== "client"
      || event.created_at !== merge.created_at
      || event.actor_client !== merge.merged_by_client
      || event.actor_session_id !== merge.merged_by_session_id
      || event.actor_model !== merge.merged_by_model
    ) throw new Error("Mnemonic returned incoherent merge relationship events.");
    return event;
  });
  if (
    relationshipEvents.length !== (result.supporting_relationship_created ? 2 : 0)
  ) throw new Error("Mnemonic returned incoherent merge relationship events.");

  const mergeEvents = result.merge_events.map((entry, index) => {
    const role = index === 0 ? "source" : "destination";
    const expectedWork = role === "source" ? source.id : destination.id;
    const event = decodeWorkEventForWork(entry, projectId, expectedWork);
    const metadata = objectValue(event.metadata);
    if (
      event.event_type !== "work_merged"
      || event.body !== merge.rationale
      || event.origin !== "live"
      || event.actor_kind !== "client"
      || event.created_at !== merge.created_at
      || event.actor_client !== merge.merged_by_client
      || event.actor_session_id !== merge.merged_by_session_id
      || event.actor_model !== merge.merged_by_model
      || !metadata
      || !sameUuid(metadata.merge_id, merge.id)
      || !sameUuid(metadata.source_work_item_id, source.id)
      || !sameUuid(metadata.destination_work_item_id, destination.id)
      || metadata.role !== role
      || metadata.source_work_version !== source.version
      || metadata.destination_work_version !== destination.version
    ) throw new Error("Mnemonic returned incoherent merge decision events.");
    return event;
  });
  if (mergeEvents.length !== 2) {
    throw new Error("Mnemonic returned incoherent merge decision events.");
  }
  const eventIds = [
    ...relationshipEvents.map((event) => event.id),
    ...mergeEvents.map((event) => event.id)
  ];
  if (new Set(eventIds).size !== eventIds.length) {
    throw new Error("Mnemonic returned duplicate merge event identities.");
  }

  return {
    merge: {
      ...merge,
      reviewed_source_revision: mergeRevision(merge.reviewed_source_revision),
      reviewed_destination_revision: mergeRevision(merge.reviewed_destination_revision)
    } as unknown as WorkMergeResult["merge"],
    source_work_item: source,
    destination_work_item: destination,
    direct_destination: directDestination,
    canonical_work_item: canonicalWorkItem,
    supporting_relationship_created: result.supporting_relationship_created,
    supporting_relationship: relationship,
    relationship_events: relationshipEvents,
    merge_events: mergeEvents
  };
}

function decodeSuccess<K extends MutationKind>(
  request: FrozenMutationRequest & { readonly kind: K },
  value: unknown
): MutationResultByKind[K] {
  const body = requestBody(request);
  let decoded: MutationResultByKind[MutationKind];
  if (request.kind === "create_work") {
    const path = parsePath(request.path, "/work-items");
    const creation = objectValue(value);
    if (!path || !creation || !exactKeys(creation, ["work_item", "initial_checkpoint", "initial_relationships"])) {
      throw new Error("Mnemonic returned an invalid mutation response.");
    }
    const workItem = decodeWorkItem(creation.work_item, path.projectId);
    if (
      workItem.version !== 1
      || workItem.title !== String(body.title).trim()
      || workItem.summary !== String(body.summary).trim()
      || workItem.priority !== (body.priority === undefined ? 0 : body.priority)
      || workItem.status !== (body.status === undefined ? "pending" : body.status)
    ) throw new Error("Mnemonic returned an incoherent mutation response.");
    const checkpoint = decodeCheckpoint(
      creation.initial_checkpoint,
      workItem.id,
      "context",
      body.initial_checkpoint
    );
    if (!sameUuid(workItem.initial_checkpoint_id, checkpoint.id) || !Array.isArray(creation.initial_relationships)) {
      throw new Error("Mnemonic returned an incoherent mutation response.");
    }
    const requestedRelationships = Array.isArray(body.initial_relationships)
      ? body.initial_relationships
      : [];
    const relationshipInputs = requestedRelationships.map((entry) => {
      const input = objectValue(entry);
      if (!input) throw new Error("The frozen mutation request is invalid.");
      return input;
    }).sort((left, right) => {
      const leftOrder = initialRelationshipOrder(left);
      const rightOrder = initialRelationshipOrder(right);
      return leftOrder < rightOrder ? -1 : leftOrder > rightOrder ? 1 : 0;
    });
    const expectedByIdentity = new Map<string, JsonObject>();
    for (const input of relationshipInputs) {
      const expected = expectedRelationship(input, workItem.id);
      const identity = expected && relationshipIdentity(
        expected.type,
        expected.source,
        expected.target
      );
      if (!expected || !identity) {
        throw new Error("The frozen mutation request is invalid.");
      }
      if (!expectedByIdentity.has(identity)) expectedByIdentity.set(identity, input);
    }
    if (creation.initial_relationships.length !== expectedByIdentity.size) {
      throw new Error("Mnemonic returned an incoherent mutation response.");
    }
    const seenRelationshipIdentities = new Set<string>();
    const relationships = creation.initial_relationships.map((entry) => {
      const structural = decodeRelationship(entry, path.projectId);
      const identity = relationshipIdentity(
        structural.relationship_type,
        structural.source_work_item_id,
        structural.target_work_item_id
      );
      const relationshipInput = identity
        ? expectedByIdentity.get(identity)
        : undefined;
      if (!identity || !relationshipInput || seenRelationshipIdentities.has(identity)) {
        throw new Error("Mnemonic returned an incoherent mutation response.");
      }
      seenRelationshipIdentities.add(identity);
      return decodeRelationship(entry, path.projectId, {
        ...relationshipInput,
        created_by_client: objectValue(body.initial_checkpoint)?.source_client,
        created_by_session_id: objectValue(body.initial_checkpoint)?.source_session_id,
        created_by_model: objectValue(body.initial_checkpoint)?.source_model ?? null
      }, workItem.id);
    });
    if (seenRelationshipIdentities.size !== expectedByIdentity.size) {
      throw new Error("Mnemonic returned an incoherent mutation response.");
    }
    decoded = { work_item: workItem, initial_checkpoint: checkpoint, initial_relationships: relationships };
  } else if (request.kind === "add_checkpoint") {
    const path = parsePath(request.path, "/checkpoints");
    if (!path?.workItemId) throw new Error("The frozen mutation request is invalid.");
    decoded = decodeCheckpoint(
      value,
      path.workItemId,
      body.kind === undefined ? "context" : String(body.kind),
      body
    );
  } else if (request.kind === "append_event") {
    const path = parsePath(request.path, "/events");
    if (!path?.workItemId) throw new Error("The frozen mutation request is invalid.");
    const event = decodeWorkEventForWork(value, path.projectId, path.workItemId);
    if (
      event.event_type !== "progress"
      || event.body !== body.body
      || event.actor_client !== objectValue(body.actor)?.actor_client
      || event.actor_session_id !== objectValue(body.actor)?.actor_session_id
      || event.actor_model !== (objectValue(body.actor)?.actor_model ?? null)
      || !jsonEqual(event.metadata, body.metadata ?? {})
    ) throw new Error("Mnemonic returned an incoherent mutation response.");
    decoded = event;
  } else if (request.kind === "add_relationship") {
    const path = parsePath(request.path, "/relationships");
    const result = objectValue(value);
    if (!path || !result || !exactKeys(result, ["relationship", "created"]) || typeof result.created !== "boolean") {
      throw new Error("Mnemonic returned an invalid mutation response.");
    }
    decoded = {
      relationship: decodeRelationship(result.relationship, path.projectId, body, undefined, result.created),
      created: result.created
    };
  } else if (request.kind === "update_work") {
    const path = parsePath(request.path, "");
    if (!path?.workItemId) throw new Error("The frozen mutation request is invalid.");
    const workItem = decodeWorkItem(value, path.projectId, path.workItemId);
    if (
      workItem.version !== Number(body.expected_version) + 1
      || (body.title !== undefined && workItem.title !== String(body.title).trim())
      || (body.summary !== undefined && workItem.summary !== String(body.summary).trim())
      || (body.priority !== undefined && workItem.priority !== body.priority)
      || (body.status !== undefined && workItem.status !== body.status)
    ) throw new Error("Mnemonic returned an incoherent mutation response.");
    decoded = workItem;
  } else if (request.kind === "defer_work") {
    const path = parsePath(request.path, "/defer");
    if (!path?.workItemId) throw new Error("The frozen mutation request is invalid.");
    const workItem = decodeWorkItem(value, path.projectId, path.workItemId);
    if (
      workItem.status !== "deferred"
      || workItem.version !== Number(body.expected_version) + 1
    ) throw new Error("Mnemonic returned an incoherent mutation response.");
    decoded = workItem;
  } else if (request.kind === "complete_work") {
    const path = parsePath(request.path, "/complete");
    const result = objectValue(value);
    if (!path?.workItemId || !result || !exactKeys(result, ["work_item", "checkpoint"])) {
      throw new Error("Mnemonic returned an invalid mutation response.");
    }
    const workItem = decodeWorkItem(result.work_item, path.projectId, path.workItemId);
    const checkpoint = decodeCheckpoint(result.checkpoint, path.workItemId, "completion", body.checkpoint);
    if (workItem.status !== "done" || workItem.version !== Number(body.expected_version) + 1) {
      throw new Error("Mnemonic returned an incoherent mutation response.");
    }
    decoded = { work_item: workItem, checkpoint };
  } else if (request.kind === "delete_work") {
    const path = parsePath(request.path, "/delete");
    const result = objectValue(value);
    if (
      !path?.workItemId
      || !result
      || !exactKeys(result, ["deleted", "project_id", "work_item_id", "version"])
      || result.deleted !== true
      || !sameUuid(result.project_id, path.projectId)
      || !sameUuid(result.work_item_id, path.workItemId)
      || result.version !== Number(body.expected_version) + 1
    ) throw new Error("Mnemonic returned an invalid mutation response.");
    decoded = result as unknown as DeletionResult;
  } else if (request.kind === "merge_work") {
    const path = parsePath(request.path, "/merge");
    if (
      !path?.workItemId
      || !exactKeys(body, [
        "destination_work_item_id", "reviewed_source_revision",
        "reviewed_destination_revision", "rationale", "merged_by_client",
        "merged_by_session_id", "merged_by_model", "client_operation_id"
      ])
      || !validUuid(body.destination_work_item_id)
      || sameUuid(body.destination_work_item_id, path.workItemId)
      || !sameMergeRevision(body.reviewed_source_revision, body.reviewed_source_revision)
      || !sameMergeRevision(
        body.reviewed_destination_revision,
        body.reviewed_destination_revision
      )
      || !boundedText(body.rationale, 4_000)
      || body.merged_by_client !== "dashboard"
      || !boundedText(body.merged_by_session_id, 200)
      || body.merged_by_model !== null
    ) throw new Error("The frozen merge request is invalid.");
    decoded = decodeMergeResult(value, path.projectId, path.workItemId, body);
  } else if (request.kind === "resolve_human_input") {
    const path = parseGateResolutionPath(request.path);
    if (!path || !boundedText(body.resolution, 4_000)) {
      throw new Error("The frozen mutation request is invalid.");
    }
    const gate = decodeHumanGate(value, {
      projectId: path.projectId,
      workItemId: path.workItemId,
      gateId: path.gateId,
      status: "resolved"
    });
    const reviewed = objectValue(body.reviewed_context_revision);
    if (
      gate.resolution !== body.resolution
      || gate.resolved_by_client !== body.resolved_by_client
      || gate.resolved_by_session_id !== body.resolved_by_session_id
      || gate.resolved_by_model !== (body.resolved_by_model ?? null)
      || !gate.resolved_context_revision
      || !sameHumanGateRevision(gate.current_context_revision, gate.resolved_context_revision)
      || !reviewed
      || !exactKeys(reviewed, [
        "work_version", "context_checkpoint_id", "relationship_event_count"
      ])
      || !finiteInteger(reviewed.work_version, 1)
      || !validUuid(reviewed.context_checkpoint_id)
      || !finiteInteger(reviewed.relationship_event_count)
      || !sameHumanGateRevision(
        gate.resolved_context_revision,
        reviewed as unknown as typeof gate.resolved_context_revision
      )
    ) throw new Error("Mnemonic returned an incoherent human-gate resolution.");
    decoded = gate;
  } else {
    const path = parsePath(request.path, "");
    const result = objectValue(value);
    if (
      !path?.relationshipId
      || !result
      || !exactKeys(result, ["project_id", "relationship_id", "removed"])
      || !sameUuid(result.project_id, path.projectId)
      || !sameUuid(result.relationship_id, path.relationshipId)
      || typeof result.removed !== "boolean"
    ) throw new Error("Mnemonic returned an invalid mutation response.");
    decoded = result as unknown as RelationshipRemovalResult;
  }
  return decoded as MutationResultByKind[K];
}

function safeError(value: unknown): {
  message: string;
  code?: string;
  category: "string" | "validation" | "application";
} | null {
  const root = objectValue(value);
  if (!root || !exactKeys(root, ERROR_ROOT_KEYS)) return null;
  if (typeof root.detail === "string") {
    if (!boundedText(root.detail, 1_000)) return null;
    return { message: root.detail, category: "string" };
  }
  if (Array.isArray(root.detail)) {
    if (!root.detail.length || root.detail.length > 50) return null;
    for (const issueValue of root.detail) {
      const issue = objectValue(issueValue);
      if (
        !issue
        || !exactKeys(issue, VALIDATION_KEYS)
        || !boundedText(issue.type, 100)
        || !boundedText(issue.msg, 500)
        || !Array.isArray(issue.loc)
        || !issue.loc.length
        || issue.loc.length > 20
        || typeof issue.loc[0] !== "string"
        || !VALIDATION_LOCATION_ROOTS.has(issue.loc[0])
        || issue.loc.some((part) => !(
          typeof part === "string" && boundedText(part, 100)
          || finiteInteger(part, 0, 10_000)
        ))
      ) return null;
    }
    return { ...detailMessage(root.detail), category: "validation" };
  }
  const detail = objectValue(root.detail);
  if (
    !detail
    || !exactKeys(detail, STRUCTURED_ERROR_KEYS)
    || !boundedText(detail.code, 100)
    || !boundedText(detail.message, 1_000)
  ) return null;
  const context = objectValue(detail.context);
  if (!context || Object.keys(context).some((key) => !SAFE_CONTEXT_KEYS.has(key))) return null;
  if (
    context.holder_client !== undefined && !boundedText(context.holder_client, 80)
    || context.expires_at !== undefined && !validUtcDateTime(context.expires_at)
    || context.fields !== undefined && (
      !Array.isArray(context.fields)
      || context.fields.some((field) => !boundedText(field, 100))
    )
    || context.canonical_work_item_id !== undefined
      && !validUuid(context.canonical_work_item_id)
  ) return null;
  return { ...detailMessage(root.detail), category: "application" };
}

function containsOperationId(value: unknown, operationId: string): boolean {
  const normalizedOperationId = operationId.toLowerCase();
  if (typeof value === "string") return value.toLowerCase().includes(normalizedOperationId);
  if (Array.isArray(value)) return value.some((entry) => containsOperationId(entry, operationId));
  const object = objectValue(value);
  return Boolean(object && Object.entries(object).some(([key, entry]) => (
    key.toLowerCase().includes(normalizedOperationId) || containsOperationId(entry, operationId)
  )));
}

export async function classifyMutationResponse<K extends MutationKind>(
  request: FrozenMutationRequest & { readonly kind: K },
  response: Response
): Promise<MutationHttpOutcome<K>> {
  let value: unknown;
  try {
    const text = await response.text();
    value = JSON.parse(text);
  } catch {
    return {
      type: "unresolved",
      message: "The mutation response was incomplete or malformed. Retry the same pending action."
    };
  }
  if (containsOperationId(value, request.operationId)) {
    return {
      type: "unresolved",
      message: "Mnemonic returned an unsafe mutation response. Retry the same pending action."
    };
  }
  if (response.status === EXPECTED_STATUS[request.kind]) {
    try {
      return { type: "success", value: decodeSuccess(request, value) };
    } catch {
      return {
        type: "unresolved",
        message: "Mnemonic returned an unexpected mutation result. Retry the same pending action."
      };
    }
  }
  if (response.status >= 200 && response.status < 300) {
    return {
      type: "unresolved",
      message: "Mnemonic returned an unexpected mutation status. Retry the same pending action."
    };
  }
  const detail = safeError(value);
  if (
    response.status === 503
    && detail?.category === "application"
    && detail.code === "client_operation_unavailable"
  ) {
    return {
      type: "unresolved",
      message: "Mnemonic cannot verify the mutation outcome yet. Retry the same pending action."
    };
  }
  if (
    response.status === 503
    && detail?.category === "application"
    && detail.code === "duplicate_graph_invalid"
  ) {
    return { type: "rejected", error: new ApiError(detail.message, 503, detail.code) };
  }
  if (AMBIGUOUS_STATUSES.has(response.status) || response.status >= 500) {
    return {
      type: "unresolved",
      message: "The mutation outcome is unknown. Retry the same pending action."
    };
  }
  if (!detail || response.status < 400) {
    return {
      type: "unresolved",
      message: "Mnemonic returned an unrecognized mutation response. Retry the same pending action."
    };
  }
  const error = new ApiError(detail.message, response.status, detail.code);
  if (response.status === 409 && detail.code === "client_operation_conflict") {
    return { type: "safety_conflict", error };
  }
  const recognized = detail.category === "validation"
    ? response.status === 422
    : detail.category === "application"
      ? Boolean(
        detail.code
        && DEFINITIVE_APPLICATION_ERRORS.get(response.status)?.has(detail.code)
      )
      : isDefinitiveProxyError(response.status, detail.message)
        || DEFINITIVE_API_STRING_ERRORS.get(response.status)?.has(detail.message) === true;
  return recognized
    ? { type: "rejected", error }
    : {
      type: "unresolved",
      message: "Mnemonic returned an unrecognized mutation response. Retry the same pending action."
    };
}
