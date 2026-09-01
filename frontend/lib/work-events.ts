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
  "work_completed",
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
  "work_created",
  "work_claimed",
  "checkpoint_added",
  "progress",
  "dependency_added",
  "relationship_added",
  "work_completed"
]);
const EVENT_FIELDS = new Set([
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
]);
const UUID_PATTERN = /^[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$/;
const UTC_DATE_TIME_PATTERN = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?Z$/;
const EVENT_SECRET_KEYS = new Set([
  "lease_token",
  "claim_request_id",
  "api_key",
  "authorization",
  "cookie",
  "secret"
]);


type JsonObject = Record<string, unknown>;

function objectValue(value: unknown): JsonObject | null {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return null;
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null
    ? value as JsonObject
    : null;
}

function exactKeys(value: JsonObject, keys: readonly string[]): boolean {
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  return actual.length === expected.length && actual.every((key, index) => key === expected[index]);
}

function finiteInteger(value: unknown, minimum = 0): value is number {
  return Number.isSafeInteger(value) && Number(value) >= minimum;
}

function nullableString(value: unknown): value is string | null {
  return value === null || typeof value === "string";
}
function validUnicode(value: string): boolean {
  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index);
    if (code >= 0xd800 && code <= 0xdbff) {
      const next = value.charCodeAt(index + 1);
      if (!(next >= 0xdc00 && next <= 0xdfff)) return false;
      index += 1;
    } else if (code >= 0xdc00 && code <= 0xdfff) {
      return false;
    }
  }
  return true;
}

function boundedText(value: unknown, maximum: number): value is string {
  return typeof value === "string"
    && validUnicode(value)
    && !value.includes("\0")
    && Array.from(value).length >= 1
    && Array.from(value).length <= maximum
    && value.trim().length > 0;
}

function validUuid(value: unknown): value is string {
  return typeof value === "string" && UUID_PATTERN.test(value);
}

function nullableUuid(value: unknown): value is string | null {
  return value === null || validUuid(value);
}

function validUtcDateTime(value: unknown): value is string {
  if (typeof value !== "string") return false;
  const match = UTC_DATE_TIME_PATTERN.exec(value);
  if (!match) return false;
  const [, yearText, monthText, dayText, hourText, minuteText, secondText] = match;
  const year = Number(yearText);
  const month = Number(monthText);
  const day = Number(dayText);
  const hour = Number(hourText);
  const minute = Number(minuteText);
  const second = Number(secondText);
  if (year < 1 || month < 1 || month > 12 || hour > 23 || minute > 59 || second > 59) {
    return false;
  }
  const leapYear = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const days = [31, leapYear ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  return day >= 1 && day <= days[month - 1]!;
}

function validEventMetadata(value: unknown): value is JsonObject {
  const stack = new WeakSet<object>();
  let separatorBytes = 0;

  const visit = (item: unknown): boolean => {
    if (item === null || typeof item === "boolean") return true;
    if (typeof item === "number") return Number.isFinite(item);
    if (typeof item === "string") return validUnicode(item) && !item.includes("\0");
    if (typeof item !== "object" || stack.has(item)) return false;
    stack.add(item);
    if (Array.isArray(item)) {
      separatorBytes += Math.max(0, item.length - 1);
      const valid = item.every(visit);
      stack.delete(item);
      return valid;
    }
    const object = objectValue(item);
    if (!object) {
      stack.delete(item);
      return false;
    }
    const entries = Object.entries(object);
    separatorBytes += entries.length ? (2 * entries.length) - 1 : 0;
    const valid = entries.every(([key, entry]) => (
      validUnicode(key)
      && !key.includes("\0")
      && !EVENT_SECRET_KEYS.has(key.toLowerCase())
      && visit(entry)
    ));
    stack.delete(item);
    return valid;
  };

  if (!objectValue(value) || !visit(value)) return false;
  try {
    const encoded = JSON.stringify(value);
    return encoded !== undefined
      && new TextEncoder().encode(encoded).byteLength + separatorBytes <= 16_384;
  } catch {
    return false;
  }
}


function isStatus(value: unknown): value is EventWorkStatus {
  return typeof value === "string" && WORK_STATUSES.has(value as EventWorkStatus);
}

function validChangeSet(value: unknown): value is WorkEventChangeSet {
  const changes = objectValue(value);
  if (!changes) return false;
  const keys = Object.keys(changes);
  if (!keys.length || keys.some((key) => !["title", "summary", "priority", "status"].includes(key))) {
    return false;
  }
  return keys.every((key) => {
    const change = objectValue(changes[key]);
    if (!change || !exactKeys(change, ["before", "after"])) return false;
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
  if (!metadata || !validEventMetadata(metadata)) return false;
  if (eventType === "work_created") {
    if (origin === "backfill") return exactKeys(metadata, []);
    const initial = objectValue(metadata.initial);
    return exactKeys(metadata, ["initial"])
      && Boolean(initial)
      && exactKeys(initial!, ["title", "summary", "status", "priority", "version"])
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
  if (eventType === "progress") {
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
  if (!event || Object.keys(event).some((key) => !EVENT_FIELDS.has(key)) || !exactKeys(event, [...EVENT_FIELDS])) {
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

export function decodeWorkEventPage(
  value: unknown,
  expectedProjectId: string,
  expectedWorkItemId: string
): WorkEventPage {
  const page = objectValue(value);
  if (
    !page
    || !exactKeys(page, [
      "items",
      "total",
      "limit",
      "offset",
      "pre_phase5_history_may_be_incomplete"
    ])
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
    || items.some((item) => (
      item.project_id.toLowerCase() !== expectedProjectId.toLowerCase()
      || item.work_item_id.toLowerCase() !== expectedWorkItemId.toLowerCase()
    ))
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
    work_completed: "Completed work",
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
  switch (event.event_type) {
    case "work_created": {
      const initial = objectValue(metadata.initial);
      return initial && typeof initial.title === "string"
        ? `Created “${initial.title}” at priority ${String(initial.priority)}.`
        : "Reconstructed the retained creation fact at the Phase 5 cutover.";
    }
    case "work_updated": {
      const changes = objectValue(metadata.changes);
      const fields = changes ? Object.keys(changes).sort() : [];
      return fields.length ? `Changed ${fields.join(", ")}.` : "Updated work fields.";
    }
    case "work_status_changed":
      return `Changed status from ${statusText(metadata.from_status)} to ${statusText(metadata.to_status)}.`;
    case "work_reopened":
      return `Reopened work from ${statusText(metadata.from_status)}.`;
    case "work_claimed":
      return typeof metadata.expires_at === "string"
        ? `Claimed until ${metadata.expires_at}.`
        : "Reconstructed a lease retained at the Phase 5 cutover.";
    case "work_released":
      return metadata.lease_holder_kind === "client"
        ? `Released the claim held by ${String(metadata.lease_holder_client)} · ${String(metadata.lease_holder_session_id)}.`
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
    case "work_completed":
      return "Completed work with an immutable completion checkpoint.";
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
  return event.event_type === "progress" ? event.body : null;
}
