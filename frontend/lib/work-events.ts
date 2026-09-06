import { validExternalReferences, validSparseReferences, referenceKeys } from "./external-references.ts";
import type {
  EventWorkStatus,
  MutationActor,
  ProgressEventInput,
  RelationshipDirection,
  RelationshipType,
  WorkEventChangeSet,
  WorkEventPage,
  WorkEventRead,
  WorkEventType
} from "@/lib/types";
import {
  boundedText,
  exactKeys,
  finiteInteger,
  nullableUuid,
  objectValue,
  sameUuid,
  validBoundedMetadata,
  validUtcDateTime,
  validUuid,
  type JsonObject
} from "./wire-guards.ts";

export const WORK_EVENT_TYPES = [
  "work_created",
  "work_updated",
  "work_status_changed",
  "work_reopened",
  "work_claimed",
  "work_released",
  "checkpoint_added",
  "progress",
  "dependency_added",
  "dependency_removed",
  "relationship_added",
  "relationship_removed",
  "human_attention_requested",
  "human_attention_resolved",
  "work_merged",
  "work_moved",
  "work_completed",
  "work_follow_up_requested", "work_follow_up_answered", "work_follow_up_superseded",
  "code_review_requested", "code_review_completed", "code_review_superseded",
  "work_deleted"
] as const satisfies readonly WorkEventType[];

export const EVENT_PAGE_SIZE = 20;

const WORK_STATUSES = new Set<EventWorkStatus>([
  "open", "pending", "deferred", "done", "wont-do", "promoted"
]);
const RELATIONSHIP_TYPES = new Set<RelationshipType>([
  "blocks",
  "parent-child",
  "discovered-from",
  "duplicate-of",
  "related"
]);
const DIRECTIONS = new Set<RelationshipDirection>(["incoming", "outgoing", "undirected"]);
const EVENT_TYPE_SET = new Set<WorkEventType>(WORK_EVENT_TYPES);
const BACKFILLABLE_EVENT_TYPES = new Set<WorkEventType>([
  "work_created",
  "work_claimed",
  "checkpoint_added",
  "dependency_added",
  "relationship_added",
  "work_completed",
  "work_deleted"
]);
const REQUIRED_LIVE_ACTOR_TYPES = new Set<WorkEventType>([
  "work_follow_up_requested", "work_follow_up_answered", "work_follow_up_superseded",
  "code_review_requested", "code_review_completed", "code_review_superseded",
  "work_created",
  "work_claimed",
  "checkpoint_added",
  "progress",
  "dependency_added",
  "relationship_added",
  "human_attention_requested",
  "human_attention_resolved",
  "work_merged",
  "work_completed"
]);
const EVENT_FIELDS = [
  "id",
  "project_id",
  "work_item_id",
  "event_type",
  "actor_kind",
  "actor_client",
  "actor_session_id",
  "actor_model",
  "body",
  "checkpoint_id",
  "lease_generation_id",
  "lease_release_id",
  "relationship_id",
  "relationship_source_work_item_id",
  "relationship_target_work_item_id",
  "relationship_context_checkpoint_work_item_id",
  "relationship_context_checkpoint_id",
  "relationship_direction",
  "counterpart_work_item_id",
  "metadata_version",
  "metadata",
  "origin",
  "created_at"
] as const;
const REVIEW_REFERENCE_FIELDS = ["code_review_id", "work_follow_up_id", "work_follow_up_answer_id", "code_review_result_id"] as const;
const REVIEW_EVENT_REFS: Partial<Record<WorkEventType, readonly string[]>> = {
  work_follow_up_requested: ["work_follow_up_id"], work_follow_up_superseded: ["work_follow_up_id"],
  work_follow_up_answered: ["work_follow_up_id", "work_follow_up_answer_id"],
  code_review_requested: ["code_review_id"], code_review_superseded: ["code_review_id"],
  code_review_completed: ["code_review_id", "code_review_result_id"]
};
const EVENT_FIELD_SET = new Set<string>([...EVENT_FIELDS, ...REVIEW_REFERENCE_FIELDS]);
const EVENT_PAGE_FIELDS = [
  "items",
  "total",
  "limit",
  "offset",
  "pre_phase5_history_may_be_incomplete"
] as const;

export const WORK_EVENT_DECODER_FIELDS = {
  EVENT_FIELDS: [...EVENT_FIELDS, ...REVIEW_REFERENCE_FIELDS],
  decodeWorkEventPage: EVENT_PAGE_FIELDS
} as const;
const EVENT_SECRET_KEYS = new Set([
  "lease_token",
  "claim_request_id",
  "api_key",
  "authorization",
  "cookie",
  "secret"
]);


function nullableString(value: unknown): value is string | null {
  return value === null || typeof value === "string";
}
function validEventMetadata(value: unknown, eventType: WorkEventType): value is JsonObject {
  return validBoundedMetadata(value, EVENT_SECRET_KEYS, ["work_created", "work_updated", "work_status_changed", "work_reopened"].includes(eventType) ? 131_072 : 16_384);
}


function isStatus(value: unknown): value is EventWorkStatus {
  return typeof value === "string" && WORK_STATUSES.has(value as EventWorkStatus);
}

function validChangeSet(value: unknown): value is WorkEventChangeSet {
  const changes = objectValue(value);
  if (!changes) return false;
  const keys = Object.keys(changes);
  if (!keys.length || keys.some((key) => !["title", "summary", "priority", "status", "external_references"].includes(key))) {
    return false;
  }
  return keys.every((key) => {
    const change = objectValue(changes[key]);
    if (!change || !exactKeys(change, ["before", "after"])) return false;
    if (key === "external_references") return validExternalReferences(change.before, true) && validExternalReferences(change.after, true);
    if (key === "priority") {
      return finiteInteger(change.before) && Number(change.before) <= 100
        && finiteInteger(change.after) && Number(change.after) <= 100;
    }
    if (key === "status") return isStatus(change.before) && isStatus(change.after);
    if (key === "title") return boundedText(change.before, 200) && boundedText(change.after, 200);
    return boundedText(change.before, 1000) && boundedText(change.after, 1000);
  });
}

function validMetadata(eventType: WorkEventType, origin: "live" | "backfill", value: unknown): boolean {
  if (origin === "backfill" && !BACKFILLABLE_EVENT_TYPES.has(eventType)) return false;
  const metadata = objectValue(value);
  if (!metadata || !validEventMetadata(metadata, eventType)) return false;
  const reviewFields = REVIEW_EVENT_REFS[eventType];
  if (reviewFields) return origin === "live" && exactKeys(metadata, reviewFields) && reviewFields.every((key) => validUuid(metadata[key]));
  if (["work_claimed", "work_released"].includes(eventType)
    && ["purpose", "code_review_id", "mode"].some((key) => Object.hasOwn(metadata, key))) {
    const { purpose, code_review_id, mode, ...ordinary } = metadata;
    return origin === "live" && purpose === "code_review" && validUuid(code_review_id)
      && ["cold", "warm"].includes(String(mode)) && validMetadata(eventType, origin, ordinary);
  }
  if (eventType === "work_created") {
    if (origin === "backfill") return exactKeys(metadata, []);
    const initial = objectValue(metadata.initial);
    return exactKeys(metadata, ["initial"])
      && Boolean(initial)
      && validSparseReferences(initial!)
      && exactKeys(initial!, referenceKeys(initial!, ["title", "summary", "status", "priority", "version"]))
      && boundedText(initial!.title, 200)
      && boundedText(initial!.summary, 1000)
      && ["open", "pending", "deferred", "wont-do", "promoted"].includes(
        String(initial!.status)
      )
      && finiteInteger(initial!.priority)
      && Number(initial!.priority) <= 100
      && initial!.version === 1;
  }
  if (eventType === "work_updated") {
    const changes = objectValue(metadata.changes);
    const statusChange = changes ? objectValue(changes.status) : null;
    return exactKeys(metadata, ["changes", "work_version"])
      && validChangeSet(metadata.changes)
      && (!statusChange || statusChange.before === statusChange.after)
      && finiteInteger(metadata.work_version, 1);
  }
  if (eventType === "work_status_changed" || eventType === "work_reopened") {
    const changes = objectValue(metadata.changes);
    const statusChange = changes ? objectValue(changes.status) : null;
    const transitionMatches = eventType === "work_reopened"
      ? ["open", "pending"].includes(String(metadata.to_status))
        && !["open", "pending"].includes(String(metadata.from_status))
      : ["open", "pending"].includes(String(metadata.from_status))
        && ["deferred", "wont-do", "promoted"].includes(String(metadata.to_status));
    return transitionMatches
      && exactKeys(metadata, ["from_status", "to_status", "changes", "work_version"])
      && isStatus(metadata.from_status)
      && isStatus(metadata.to_status)
      && validChangeSet(metadata.changes)
      && statusChange?.before === metadata.from_status
      && statusChange.after === metadata.to_status
      && finiteInteger(metadata.work_version, 1);
  }
  if (eventType === "work_claimed") {
    return origin === "live"
      ? exactKeys(metadata, ["expires_at"]) && validUtcDateTime(metadata.expires_at)
      : exactKeys(metadata, ["observed_expires_at", "expiry_basis"])
        && validUtcDateTime(metadata.observed_expires_at)
        && metadata.expiry_basis === "retained_lease_at_cutover";
  }
  if (eventType === "work_released") {
    if (metadata.lease_holder_kind === "unattributed") {
      return exactKeys(metadata, ["lease_holder_kind"]);
    }
    return metadata.lease_holder_kind === "client"
      && exactKeys(metadata, [
        "lease_holder_kind",
        "lease_holder_client",
        "lease_holder_session_id"
      ])
      && boundedText(metadata.lease_holder_client, 80)
      && boundedText(metadata.lease_holder_session_id, 200);
  }
  if (eventType === "checkpoint_added") {
    return exactKeys(metadata, ["checkpoint_kind"])
      && (metadata.checkpoint_kind === "context" || metadata.checkpoint_kind === "progress");
  }
  if (eventType === "progress") return origin === "live";
  if ([
    "dependency_added",
    "dependency_removed",
    "relationship_added",
    "relationship_removed"
  ].includes(eventType)) {
    if (
      !exactKeys(metadata, ["relationship_type"])
      || typeof metadata.relationship_type !== "string"
      || !RELATIONSHIP_TYPES.has(metadata.relationship_type as RelationshipType)
    ) {
      return false;
    }
    const dependency = eventType.startsWith("dependency_");
    return dependency
      ? metadata.relationship_type === "blocks"
      : metadata.relationship_type !== "blocks";
  }
  if (
    eventType === "human_attention_requested"
    || eventType === "human_attention_resolved"
  ) {
    return origin === "live"
      && exactKeys(metadata, ["gate_id", "gate_type"])
      && validUuid(metadata.gate_id)
      && metadata.gate_type === "human";
  }
  if (eventType === "work_merged") {
    const source = metadata.source_work_item_id;
    const destination = metadata.destination_work_item_id;
    const role = metadata.role;
    return origin === "live"
      && exactKeys(metadata, [
        "merge_id",
        "source_work_item_id",
        "destination_work_item_id",
        "role",
        "source_work_version",
        "destination_work_version"
      ])
      && validUuid(metadata.merge_id)
      && validUuid(source)
      && validUuid(destination)
      && source.toLowerCase() !== destination.toLowerCase()
      && (role === "source" || role === "destination")
      && finiteInteger(metadata.source_work_version, 1)
      && finiteInteger(metadata.destination_work_version, 1);
  }
  if (eventType === "work_moved") {
    const source = metadata.source_project_id;
    const target = metadata.target_project_id;
    return origin === "live"
      && exactKeys(metadata, [
        "move_id", "source_project_id", "target_project_id", "role", "work_version"
      ])
      && validUuid(metadata.move_id)
      && validUuid(source)
      && validUuid(target)
      && source.toLowerCase() !== target.toLowerCase()
      && (metadata.role === "source" || metadata.role === "target")
      && finiteInteger(metadata.work_version, 2);
  }
  if (eventType === "work_completed") {
    return origin === "backfill"
      ? exactKeys(metadata, [])
      : exactKeys(metadata, ["from_status", "to_status", "work_version"])
        && ["open", "pending"].includes(String(metadata.from_status))
        && metadata.to_status === "done"
        && finiteInteger(metadata.work_version, 1);
  }
  return eventType === "work_deleted"
    && exactKeys(metadata, ["final_status", "final_version"])
    && isStatus(metadata.final_status)
    && finiteInteger(metadata.final_version, 1);
}

function validReferences(event: JsonObject): boolean {
  const eventType = event.event_type as WorkEventType;
  const relationshipEvent = [
    "dependency_added",
    "dependency_removed",
    "relationship_added",
    "relationship_removed"
  ].includes(eventType);
  const contextPairMatches = (event.relationship_context_checkpoint_work_item_id === null)
    === (event.relationship_context_checkpoint_id === null);
  if (!contextPairMatches) return false;
  if (eventType === "work_moved") {
    const metadata = objectValue(event.metadata);
    const projectId = metadata?.role === "source"
      ? metadata.source_project_id
      : metadata?.target_project_id;
    if (!sameUuid(event.project_id, projectId)) return false;
  }
  if (
    eventType === "progress"
    || eventType === "human_attention_requested"
    || eventType === "human_attention_resolved"
    || eventType === "work_merged"
  ) {
    if (!boundedText(event.body, 4000)) return false;
  } else if (event.body !== null) {
    return false;
  }
  if (["work_created", "checkpoint_added", "work_completed"].includes(eventType)) {
    return event.checkpoint_id !== null
      && event.lease_generation_id === null
      && event.lease_release_id === null
      && event.relationship_id === null
      && event.relationship_source_work_item_id === null
      && event.relationship_target_work_item_id === null
      && event.relationship_context_checkpoint_work_item_id === null
      && event.relationship_context_checkpoint_id === null
      && event.relationship_direction === null
      && event.counterpart_work_item_id === null;
  }
  if (eventType === "work_claimed") {
    return event.checkpoint_id === null
      && event.lease_generation_id !== null
      && event.lease_release_id === null
      && event.relationship_id === null
      && event.relationship_source_work_item_id === null
      && event.relationship_target_work_item_id === null
      && event.relationship_context_checkpoint_work_item_id === null
      && event.relationship_context_checkpoint_id === null
      && event.relationship_direction === null
      && event.counterpart_work_item_id === null;
  }
  if (eventType === "work_released") {
    return event.checkpoint_id === null
      && event.lease_generation_id !== null
      && event.lease_release_id !== null
      && event.relationship_id === null
      && event.relationship_source_work_item_id === null
      && event.relationship_target_work_item_id === null
      && event.relationship_context_checkpoint_work_item_id === null
      && event.relationship_context_checkpoint_id === null
      && event.relationship_direction === null
      && event.counterpart_work_item_id === null;
  }
  if (eventType === "work_merged") {
    const metadata = objectValue(event.metadata);
    const expectedWorkId = metadata?.role === "source"
      ? metadata.source_work_item_id
      : metadata?.destination_work_item_id;
    return sameUuid(event.work_item_id, expectedWorkId)
      && event.checkpoint_id === null
      && event.lease_generation_id === null
      && event.lease_release_id === null
      && event.relationship_id === null
      && event.relationship_source_work_item_id === null
      && event.relationship_target_work_item_id === null
      && event.relationship_context_checkpoint_work_item_id === null
      && event.relationship_context_checkpoint_id === null
      && event.relationship_direction === null
      && event.counterpart_work_item_id === null;
  }
  if (relationshipEvent) {
    const metadata = objectValue(event.metadata);
    const relationshipType = metadata?.relationship_type;
    const related = metadata?.relationship_type === "related";
    const source = event.relationship_source_work_item_id;
    const target = event.relationship_target_work_item_id;
    const contextOwner = event.relationship_context_checkpoint_work_item_id;
    const endpointProjectionMatches = source !== target
      && (event.work_item_id === source || event.work_item_id === target)
      && event.counterpart_work_item_id === (
        event.work_item_id === source ? target : source
      );
    const directionMatches = related
      ? event.relationship_direction === "undirected"
      : event.relationship_direction === (
        event.work_item_id === target ? "incoming" : "outgoing"
      );
    const contextOwnerMatches = contextOwner === null
      || contextOwner === source
      || contextOwner === target;
    const discoveredFromContextMatches = relationshipType !== "discovered-from"
      || (
        event.relationship_context_checkpoint_id !== null
        && contextOwner === target
      );
    const relatedEndpointsNormalized = !related
      || (
        typeof source === "string"
        && typeof target === "string"
        && source.toLowerCase() < target.toLowerCase()
      );
    return event.checkpoint_id === null
      && event.lease_generation_id === null
      && event.lease_release_id === null
      && event.relationship_id !== null
      && source !== null
      && target !== null
      && endpointProjectionMatches
      && directionMatches
      && event.counterpart_work_item_id !== null
      && contextOwnerMatches
      && discoveredFromContextMatches
      && relatedEndpointsNormalized;
  }
  return event.checkpoint_id === null
    && event.lease_generation_id === null
    && event.lease_release_id === null
    && event.relationship_id === null
    && event.relationship_source_work_item_id === null
    && event.relationship_target_work_item_id === null
    && event.relationship_context_checkpoint_work_item_id === null
    && event.relationship_context_checkpoint_id === null
    && event.relationship_direction === null
    && event.counterpart_work_item_id === null;
}

function validActorMatrix(event: JsonObject): boolean {
  if (event.origin === "live" && REQUIRED_LIVE_ACTOR_TYPES.has(event.event_type as WorkEventType)) {
    return event.actor_kind === "client";
  }
  if (event.origin === "backfill" && event.event_type === "work_deleted") {
    return event.actor_kind === "unattributed";
  }
  return true;
}

function invalidEventResponse(): never {
  throw new Error("Mnemonic returned an invalid work-event response.");
}

export function decodeWorkEvent(value: unknown): WorkEventRead {
  const event = objectValue(value);
  if (
    !event
    || Object.keys(event).some((key) => !EVENT_FIELD_SET.has(key))
    || !exactKeys(event, [...EVENT_FIELDS, ...REVIEW_REFERENCE_FIELDS.filter((key) => Object.hasOwn(event, key))])
  ) {
    return invalidEventResponse();
  }
  if (
    !finiteInteger(event.id, 1)
    || !validUuid(event.project_id)
    || !validUuid(event.work_item_id)
    || typeof event.event_type !== "string"
    || !EVENT_TYPE_SET.has(event.event_type as WorkEventType)
    || (event.actor_kind !== "client" && event.actor_kind !== "unattributed")
    || !nullableString(event.actor_client)
    || !nullableString(event.actor_session_id)
    || !nullableString(event.actor_model)
    || !nullableString(event.body)
    || !nullableUuid(event.checkpoint_id)
    || !nullableUuid(event.lease_generation_id)
    || !nullableUuid(event.lease_release_id)
    || !nullableUuid(event.relationship_id)
    || !nullableUuid(event.relationship_source_work_item_id)
    || !nullableUuid(event.relationship_target_work_item_id)
    || !nullableUuid(event.relationship_context_checkpoint_work_item_id)
    || !nullableUuid(event.relationship_context_checkpoint_id)
    || !(event.relationship_direction === null
      || (typeof event.relationship_direction === "string"
        && DIRECTIONS.has(event.relationship_direction as RelationshipDirection)))
    || !nullableUuid(event.counterpart_work_item_id)
    || event.metadata_version !== 1
    || (event.origin !== "live" && event.origin !== "backfill")
    || !validUtcDateTime(event.created_at)
  ) {
    return invalidEventResponse();
  }
  if (event.actor_kind === "client") {
    if (
      !boundedText(event.actor_client, 80)
      || !boundedText(event.actor_session_id, 200)
      || !(event.actor_model === null || boundedText(event.actor_model, 120))
    ) return invalidEventResponse();
  } else if (event.actor_client !== null || event.actor_session_id !== null || event.actor_model !== null) {
    return invalidEventResponse();
  }
  if (!validActorMatrix(event) || !validReferences(event) || !validMetadata(
    event.event_type as WorkEventType,
    event.origin as "live" | "backfill",
    event.metadata
  )) {
    return invalidEventResponse();
  }
  const reviewFields = REVIEW_EVENT_REFS[event.event_type as WorkEventType]
    ?? (["work_claimed", "work_released"].includes(String(event.event_type)) && objectValue(event.metadata)?.purpose === "code_review" ? ["code_review_id"] : []);
  if (!exactKeys(Object.fromEntries(REVIEW_REFERENCE_FIELDS.filter((key) => Object.hasOwn(event, key)).map((key) => [key, event[key]])), reviewFields)
    || reviewFields.some((key) => !validUuid(event[key]) || !sameUuid(event[key], objectValue(event.metadata)?.[key]))) return invalidEventResponse();
  return event as unknown as WorkEventRead;
}

export function decodeWorkEventForWork(
  value: unknown,
  expectedProjectId: string,
  expectedWorkItemId: string
): WorkEventRead {
  const event = decodeWorkEvent(value);
  if (
    !validUuid(expectedProjectId)
    || !validUuid(expectedWorkItemId)
    || event.project_id.toLowerCase() !== expectedProjectId.toLowerCase()
    || event.work_item_id.toLowerCase() !== expectedWorkItemId.toLowerCase()
  ) {
    return invalidEventResponse();
  }
  return event;
}

export function decodeHistoricalWorkEventForWork(
  value: unknown,
  expectedWorkItemId: string
): WorkEventRead {
  const event = decodeWorkEvent(value);
  if (
    !validUuid(expectedWorkItemId)
    || event.work_item_id.toLowerCase() !== expectedWorkItemId.toLowerCase()
  ) {
    return invalidEventResponse();
  }
  return event;
}

export function decodeWorkEventPage(
  value: unknown,
  expectedProjectId: string,
  expectedWorkItemId: string
): WorkEventPage {
  const page = objectValue(value);
  if (
    !page
    || !exactKeys(page, EVENT_PAGE_FIELDS)
    || !Array.isArray(page.items)
    || !finiteInteger(page.total)
    || !finiteInteger(page.limit, 1)
    || Number(page.limit) > 100
    || !finiteInteger(page.offset)
    || page.items.length > Number(page.limit)
    || (page.items.length > 0
      && Number(page.offset) + page.items.length > Number(page.total))
    || typeof page.pre_phase5_history_may_be_incomplete !== "boolean"
  ) {
    throw new Error("Mnemonic returned an invalid work-event page.");
  }
  const items = page.items.map(decodeWorkEvent);
  if (
    !validUuid(expectedProjectId)
    || !validUuid(expectedWorkItemId)
    || items.some((item) => !sameUuid(item.work_item_id, expectedWorkItemId))
  ) {
    throw new Error("Mnemonic returned an invalid work-event page.");
  }
  return {
    items,
    total: page.total,
    limit: page.limit,
    offset: page.offset,
    pre_phase5_history_may_be_incomplete: page.pre_phase5_history_may_be_incomplete
  };
}

export function dashboardMutationActor(sessionId: string): MutationActor {
  return { actor_client: "dashboard", actor_session_id: sessionId };
}

export function progressEventInput(
  body: string,
  sessionId: string,
  metadata: Record<string, unknown> = {}
): ProgressEventInput {
  return {
    event_type: "progress",
    body,
    metadata,
    actor: dashboardMutationActor(sessionId)
  };
}

export function workEventSearchParams(input: {
  eventType?: WorkEventType | "";
  limit?: number;
  offset?: number;
}): URLSearchParams {
  const params = new URLSearchParams({
    order: "newest",
    limit: String(input.limit ?? EVENT_PAGE_SIZE),
    offset: String(input.offset ?? 0)
  });
  if (input.eventType) params.set("event_type", input.eventType);
  return params;
}

export function resetNewestEventOffset(): 0 {
  return 0;
}

export function workEventTitle(eventType: WorkEventType): string {
  return {
    work_created: "Created work",
    work_updated: "Updated work",
    work_status_changed: "Changed status",
    work_reopened: "Reopened work",
    work_claimed: "Claimed work",
    work_released: "Released claim",
    checkpoint_added: "Added checkpoint",
    progress: "Progress update",
    dependency_added: "Added dependency",
    dependency_removed: "Removed dependency",
    relationship_added: "Added relationship",
    relationship_removed: "Removed relationship",
    human_attention_requested: "Requested human attention",
    human_attention_resolved: "Resolved human attention",
    work_merged: "Merged duplicate work",
    work_moved: "Moved work",
    work_completed: "Completed work",
    work_follow_up_requested: "Requested author recommendation",
    work_follow_up_answered: "Recorded author recommendation",
    work_follow_up_superseded: "Superseded author recommendation",
    code_review_requested: "Requested code review",
    code_review_completed: "Completed code review",
    code_review_superseded: "Superseded code review",
    work_deleted: "Deleted work"
  }[eventType];
}

function metadataOf(event: WorkEventRead): JsonObject {
  return event.metadata as JsonObject;
}

function statusText(value: unknown): string {
  return typeof value === "string" ? value.replace("wont-do", "won’t do") : "unknown";
}

function counterpartText(event: WorkEventRead, title?: string): string {
  if (title) return `“${title}”`;
  return event.counterpart_work_item_id
    ? `work ${event.counterpart_work_item_id}`
    : "the other work item";
}

export function relationshipEventDescription(event: WorkEventRead, counterpartTitle?: string): string {
  const metadata = metadataOf(event);
  const relationshipType = metadata.relationship_type as RelationshipType | undefined;
  const counterpart = counterpartText(event, counterpartTitle);
  const removed = event.event_type.endsWith("_removed");
  const verb = removed ? "Removed" : "Added";
  if (relationshipType === "related") return `${verb} a related-work link with ${counterpart}.`;
  if (relationshipType === "blocks") {
    return event.relationship_direction === "incoming"
      ? `${verb} a dependency where ${counterpart} blocks this work.`
      : `${verb} a dependency where this work blocks ${counterpart}.`;
  }
  if (relationshipType === "parent-child") {
    return event.relationship_direction === "incoming"
      ? `${verb} ${counterpart} as this work’s parent.`
      : `${verb} ${counterpart} as this work’s child.`;
  }
  if (relationshipType === "discovered-from") {
    return event.relationship_direction === "incoming"
      ? `${verb} a link showing ${counterpart} was discovered from this work.`
      : `${verb} a link showing this work was discovered from ${counterpart}.`;
  }
  if (relationshipType === "duplicate-of") {
    return event.relationship_direction === "incoming"
      ? `${verb} a link marking ${counterpart} as a duplicate of this work.`
      : `${verb} a link marking this work as a duplicate of ${counterpart}.`;
  }
  return `${verb} a relationship with ${counterpart}.`;
}

export function workEventDescription(event: WorkEventRead, counterpartTitle?: string): string | null {
  const metadata = metadataOf(event);
  const humanDecision = event.actor_kind === "client"
    && event.actor_client === "dashboard"
    && event.actor_model === null;
  switch (event.event_type) {
    case "work_follow_up_requested": return "Asked the originating session whether this completed work needs an adversarial review.";
    case "work_follow_up_answered": return "Recorded the originating session's review recommendation and rationale.";
    case "work_follow_up_superseded": return "Reopening superseded the unanswered recommendation without inferring an answer.";
    case "code_review_requested": return "Requested an adversarial review on this completed implementation episode.";
    case "code_review_completed": return "Recorded the immutable review result; all actionable findings share one remediation work item.";
    case "code_review_superseded": return "Reopening superseded this review request and invalidated its review lease.";
    case "work_created": {
      const initial = objectValue(metadata.initial);
      return initial && typeof initial.title === "string"
        ? `Created “${initial.title}” at priority ${String(initial.priority)}.`
        : "Reconstructed the retained creation fact at the Phase 5 cutover.";
    }
    case "work_updated": {
      const changes = objectValue(metadata.changes);
      const fields = changes ? Object.keys(changes).sort().map((field) => field === "external_references" ? "External references" : field) : [];
      return fields.length ? `Changed ${fields.join(", ")}.` : "Updated work fields.";
    }
    case "work_status_changed":
      return `${humanDecision ? "Explicit human decision: changed" : "Changed"} status from ${statusText(metadata.from_status)} to ${statusText(metadata.to_status)}.`;
    case "work_reopened":
      return `${humanDecision ? "Explicit human decision: reopened" : "Reopened"} work from ${statusText(metadata.from_status)}.`;
    case "work_claimed":
      return typeof metadata.expires_at === "string"
        ? `${humanDecision ? "Explicit human decision: claimed" : "Claimed"} until ${metadata.expires_at}.`
        : "Reconstructed a lease retained at the Phase 5 cutover.";
    case "work_released":
      return metadata.lease_holder_kind === "client"
        ? `${humanDecision ? "Explicit human decision: released" : "Released"} the claim held by ${String(metadata.lease_holder_client)} · ${String(metadata.lease_holder_session_id)}.`
        : "Released a claim whose earlier holder provenance was unavailable.";
    case "checkpoint_added":
      return `Referenced a ${String(metadata.checkpoint_kind)} checkpoint; its exact text remains in Checkpoints.`;
    case "progress":
      return null;
    case "dependency_added":
    case "dependency_removed":
    case "relationship_added":
    case "relationship_removed":
      return relationshipEventDescription(event, counterpartTitle);
    case "human_attention_requested":
      return "Requested an explicit human decision. The question remains paired with its durable gate record.";
    case "human_attention_resolved":
      return "Recorded a human-facing answer. The answer does not execute or authorize another action.";
    case "work_merged":
      return metadata.role === "source"
        ? `Made this work an immutable duplicate of ${String(metadata.destination_work_item_id)}.`
        : `Kept this work canonical when ${String(metadata.source_work_item_id)} was merged into it.`;
    case "work_moved":
      return metadata.role === "source"
        ? `Moved this work to project ${String(metadata.target_project_id)}.`
        : `Moved this work here from project ${String(metadata.source_project_id)}.`;
    case "work_completed":
      return humanDecision
        ? "Explicit human decision: completed work with an immutable completion checkpoint."
        : "Completed work with an immutable completion checkpoint.";
    case "work_deleted":
      return `Soft-deleted work in ${statusText(metadata.final_status)} status.`;
  }
}

export function workEventActorLabel(event: WorkEventRead): string {
  return event.actor_kind === "client" && event.actor_client && event.actor_session_id
    ? `${event.actor_client} · ${event.actor_session_id}`
    : "Unattributed earlier action";
}

export function safeEventBody(event: WorkEventRead): string | null {
  return [
    "progress", "human_attention_requested", "human_attention_resolved", "work_merged"
  ].includes(event.event_type) ? event.body : null;
}
