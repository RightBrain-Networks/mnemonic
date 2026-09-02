import type {
  AdjacentRelationshipRead,
  CheckpointPointer,
  HumanAttentionItem,
  HumanAttentionPage,
  HumanGateContextRevision,
  HumanGatePage,
  HumanGateRead,
  HumanGateStatus,
  LeasePublic,
  Readiness,
  WorkIdentityPointer,
  WorkContext,
  WorkStatus,
  WorkSummary
} from "@/lib/types";
import {
  boundedText,
  decodeWorkItem,
  exactKeys,
  finiteInteger,
  nullableBoundedText,
  objectValue,
  sameUuid,
  validUtcDateTime,
  validUuid,
  type JsonObject
} from "./wire-guards.ts";
const WORK_STATUSES = new Set<WorkStatus>([
  "pending", "deferred", "done", "wont-do", "promoted"
]);
const DISPLAY_STATES = new Set([
  ...WORK_STATUSES,
  "active",
  "dropped",
  "blocked",
  "waiting"
]);
const GATE_FIELDS = [
  "id", "project_id", "work_item_id", "gate_type", "question",
  "requested_by_client", "requested_by_session_id", "requested_by_model",
  "requested_context_revision", "created_at", "status",
  "current_context_revision", "work_changed_since_request",
  "context_checkpoint_changed_since_request", "relationships_changed_since_request",
  "context_changed_since_request", "resolved_at", "resolution", "resolved_by_client",
  "resolved_by_session_id", "resolved_by_model", "resolved_context_revision",
  "context_changed_at_resolution"
] as const;
const REVISION_FIELDS = [
  "work_version", "context_checkpoint_id", "relationship_event_count"
] as const;
const CHECKPOINT_POINTER_FIELDS = [
  "id", "work_item_id", "kind", "source_client", "source_session_id", "source_model",
  "repository_branch", "verified_against", "tags", "migration_origin", "legacy_record_id",
  "created_at"
] as const;
const LEASE_FIELDS = [
  "holder_client", "holder_session_id", "acquired_at", "renewed_at", "expires_at"
] as const;
const READINESS_FIELDS = [
  "lifecycle_status", "is_terminal", "has_active_lease", "has_dropped_lease",
  "active_lease", "unresolved_blocker_count", "is_blocked", "unresolved_gate_count",
  "is_gated", "is_ready", "display_state"
] as const;
const ANCESTOR_FIELDS = ["id", "title", "status"] as const;
const WORK_SUMMARY_FIELDS = [
  "work_item", "checkpoint_count", "ancestor_path", "ancestor_path_truncated",
  "current_context", "readiness"
] as const;
const CURSOR_PAGE_FIELDS = ["items", "total", "limit", "next_cursor"] as const;
const HUMAN_ATTENTION_ITEM_FIELDS = ["gate", "summary"] as const;

export const HUMAN_GATE_DECODER_FIELDS = {
  GATE_FIELDS,
  decodeAncestor: ANCESTOR_FIELDS,
  decodeCheckpointPointer: CHECKPOINT_POINTER_FIELDS,
  "decodeCursorPage:attention": CURSOR_PAGE_FIELDS,
  "decodeCursorPage:gates": CURSOR_PAGE_FIELDS,
  "decodeHumanAttentionPage:item": HUMAN_ATTENTION_ITEM_FIELDS,
  decodeLease: LEASE_FIELDS,
  decodeReadiness: READINESS_FIELDS,
  decodeRevision: REVISION_FIELDS,
  decodeWorkSummary: WORK_SUMMARY_FIELDS
} as const;

function decodeRevision(value: unknown): HumanGateContextRevision {
  const revision = objectValue(value);
  if (
    !revision
    || !exactKeys(revision, REVISION_FIELDS)
    || !finiteInteger(revision.work_version, 1)
    || !validUuid(revision.context_checkpoint_id)
    || !finiteInteger(revision.relationship_event_count)
  ) throw new Error("Mnemonic returned an invalid human-gate revision.");
  return revision as unknown as HumanGateContextRevision;
}

export function sameHumanGateRevision(
  left: HumanGateContextRevision,
  right: HumanGateContextRevision
): boolean {
  return left.work_version === right.work_version
    && left.relationship_event_count === right.relationship_event_count
    && sameUuid(left.context_checkpoint_id, right.context_checkpoint_id);
}

export function humanGateProjectionKey(gate: HumanGateRead): string {
  return [
    gate.requested_context_revision.work_version,
    gate.requested_context_revision.context_checkpoint_id.toLowerCase(),
    gate.requested_context_revision.relationship_event_count,
    gate.current_context_revision.work_version,
    gate.current_context_revision.context_checkpoint_id.toLowerCase(),
    gate.current_context_revision.relationship_event_count,
    gate.work_changed_since_request ? 1 : 0,
    gate.context_checkpoint_changed_since_request ? 1 : 0,
    gate.relationships_changed_since_request ? 1 : 0,
    gate.context_changed_since_request ? 1 : 0
  ].join(":");
}

export function decodeHumanGate(
  value: unknown,
  expected?: { projectId?: string; workItemId?: string; gateId?: string; status?: HumanGateStatus }
): HumanGateRead {
  const gate = objectValue(value);
  if (
    !gate
    || !exactKeys(gate, GATE_FIELDS)
    || !validUuid(gate.id)
    || !validUuid(gate.project_id)
    || !validUuid(gate.work_item_id)
    || gate.gate_type !== "human"
    || !boundedText(gate.question, 4_000)
    || !boundedText(gate.requested_by_client, 80)
    || !boundedText(gate.requested_by_session_id, 200)
    || !nullableBoundedText(gate.requested_by_model, 120)
    || !validUtcDateTime(gate.created_at)
    || (gate.status !== "unresolved" && gate.status !== "resolved")
    || typeof gate.work_changed_since_request !== "boolean"
    || typeof gate.context_checkpoint_changed_since_request !== "boolean"
    || typeof gate.relationships_changed_since_request !== "boolean"
    || typeof gate.context_changed_since_request !== "boolean"
    || expected?.projectId !== undefined && !sameUuid(gate.project_id, expected.projectId)
    || expected?.workItemId !== undefined && !sameUuid(gate.work_item_id, expected.workItemId)
    || expected?.gateId !== undefined && !sameUuid(gate.id, expected.gateId)
    || expected?.status !== undefined && gate.status !== expected.status
  ) throw new Error("Mnemonic returned an invalid human gate.");

  decodeRevision(gate.requested_context_revision);
  decodeRevision(gate.current_context_revision);

  if (gate.status === "unresolved") {
    if (
      gate.resolved_at !== null
      || gate.resolution !== null
      || gate.resolved_by_client !== null
      || gate.resolved_by_session_id !== null
      || gate.resolved_by_model !== null
      || gate.resolved_context_revision !== null
      || gate.context_changed_at_resolution !== null
    ) throw new Error("Mnemonic returned an incoherent unresolved human gate.");
  } else {
    if (
      !validUtcDateTime(gate.resolved_at)
      || !boundedText(gate.resolution, 4_000)
      || !boundedText(gate.resolved_by_client, 80)
      || !boundedText(gate.resolved_by_session_id, 200)
      || !nullableBoundedText(gate.resolved_by_model, 120)
      || typeof gate.context_changed_at_resolution !== "boolean"
    ) throw new Error("Mnemonic returned an incoherent resolved human gate.");
    decodeRevision(gate.resolved_context_revision);
    if (Date.parse(gate.resolved_at) < Date.parse(String(gate.created_at))) {
      throw new Error("Mnemonic returned an incoherent resolved human gate.");
    }
  }
  return gate as unknown as HumanGateRead;
}

function decodeCheckpointPointer(value: unknown, workItemId: string): CheckpointPointer {
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
  ) throw new Error("Mnemonic returned an invalid attention checkpoint pointer.");
  return checkpoint as unknown as CheckpointPointer;
}

function decodeLease(value: unknown): LeasePublic | null {
  if (value === null) return null;
  const lease = objectValue(value);
  if (
    !lease
    || !exactKeys(lease, LEASE_FIELDS)
    || !boundedText(lease.holder_client, 80)
    || !boundedText(lease.holder_session_id, 200)
    || !validUtcDateTime(lease.acquired_at)
    || !validUtcDateTime(lease.renewed_at)
    || !validUtcDateTime(lease.expires_at)
  ) throw new Error("Mnemonic returned an invalid attention lease.");
  return lease as unknown as LeasePublic;
}

function decodeReadiness(value: unknown, status: WorkStatus): Readiness {
  const readiness = objectValue(value);
  if (
    !readiness
    || !exactKeys(readiness, READINESS_FIELDS)
    || readiness.lifecycle_status !== status
    || typeof readiness.is_terminal !== "boolean"
    || typeof readiness.has_active_lease !== "boolean"
    || typeof readiness.has_dropped_lease !== "boolean"
    || !finiteInteger(readiness.unresolved_blocker_count)
    || typeof readiness.is_blocked !== "boolean"
    || !finiteInteger(readiness.unresolved_gate_count)
    || typeof readiness.is_gated !== "boolean"
    || typeof readiness.is_ready !== "boolean"
    || typeof readiness.display_state !== "string"
    || !DISPLAY_STATES.has(readiness.display_state)
  ) throw new Error("Mnemonic returned invalid attention readiness.");
  const lease = decodeLease(readiness.active_lease);
  const terminal = ["done", "wont-do", "promoted"].includes(status);
  const ready = status === "pending"
    && lease === null
    && readiness.unresolved_blocker_count === 0
    && readiness.unresolved_gate_count === 0;
  const displayState = status !== "pending"
    ? status
    : readiness.unresolved_gate_count > 0
      ? "waiting"
      : readiness.unresolved_blocker_count > 0
        ? "blocked"
        : lease !== null
          ? "active"
          : readiness.has_dropped_lease
            ? "dropped"
            : "pending";
  if (
    readiness.is_terminal !== terminal
    || readiness.has_active_lease !== (lease !== null)
    || readiness.is_blocked !== (readiness.unresolved_blocker_count > 0)
    || readiness.is_gated !== (readiness.unresolved_gate_count > 0)
    || readiness.is_ready !== ready
    || readiness.display_state !== displayState
  ) throw new Error("Mnemonic returned incoherent attention readiness.");
  return { ...readiness, active_lease: lease } as unknown as Readiness;
}

function decodeAncestor(value: unknown): WorkIdentityPointer {
  const ancestor = objectValue(value);
  if (
    !ancestor
    || !exactKeys(ancestor, ANCESTOR_FIELDS)
    || !validUuid(ancestor.id)
    || !boundedText(ancestor.title, 200)
    || typeof ancestor.status !== "string"
    || !WORK_STATUSES.has(ancestor.status as WorkStatus)
  ) throw new Error("Mnemonic returned an invalid attention ancestry path.");
  return ancestor as unknown as WorkIdentityPointer;
}

export function decodeWorkSummary(value: unknown, projectId: string): WorkSummary {
  const summary = objectValue(value);
  if (
    !summary
    || !exactKeys(summary, WORK_SUMMARY_FIELDS)
  ) throw new Error("Mnemonic returned an invalid attention work summary.");
  const workItem = decodeWorkItem(
    summary.work_item,
    projectId,
    undefined,
    "Mnemonic returned an invalid attention work summary."
  );
  if (
    !finiteInteger(summary.checkpoint_count, 1)
    || !Array.isArray(summary.ancestor_path)
    || summary.ancestor_path.length > 50
    || typeof summary.ancestor_path_truncated !== "boolean"
  ) throw new Error("Mnemonic returned an invalid attention work summary.");
  const ancestors = summary.ancestor_path.map(decodeAncestor);
  if (new Set(ancestors.map((item) => item.id.toLowerCase())).size !== ancestors.length) {
    throw new Error("Mnemonic returned an invalid attention ancestry path.");
  }
  return {
    work_item: workItem,
    checkpoint_count: summary.checkpoint_count,
    ancestor_path: ancestors,
    ancestor_path_truncated: summary.ancestor_path_truncated,
    current_context: decodeCheckpointPointer(summary.current_context, workItem.id),
    readiness: decodeReadiness(summary.readiness, workItem.status)
  };
}

function decodeCursorPage(
  value: unknown,
  decodeItem: (item: unknown) => unknown,
  expectedLimit?: number
): { items: unknown[]; total: number; limit: number; next_cursor: string | null } {
  const page = objectValue(value);
  if (
    !page
    || !exactKeys(page, CURSOR_PAGE_FIELDS)
    || !Array.isArray(page.items)
    || !finiteInteger(page.total)
    || !finiteInteger(page.limit, 0, 100)
    || expectedLimit !== undefined && page.limit !== expectedLimit
    || page.items.length > Number(page.limit)
    || page.items.length > Number(page.total)
    || !(page.next_cursor === null || boundedText(page.next_cursor, 4_096))
    || page.limit === 0 && (page.items.length !== 0 || page.next_cursor !== null)
  ) throw new Error("Mnemonic returned an invalid cursor page.");
  return {
    items: page.items.map(decodeItem),
    total: page.total,
    limit: page.limit,
    next_cursor: page.next_cursor
  };
}

export function decodeHumanAttentionPage(
  value: unknown,
  projectId: string,
  options: { workItemId?: string; limit?: number } = {}
): HumanAttentionPage {
  if (!validUuid(projectId) || options.workItemId !== undefined && !validUuid(options.workItemId)) {
    throw new Error("The expected human-attention scope is invalid.");
  }
  const decoded = decodeCursorPage(value, (itemValue) => {
    const item = objectValue(itemValue);
    if (!item || !exactKeys(item, HUMAN_ATTENTION_ITEM_FIELDS)) {
      throw new Error("Mnemonic returned an invalid human-attention item.");
    }
    const gate = decodeHumanGate(item.gate, {
      projectId,
      workItemId: options.workItemId,
      status: "unresolved"
    });
    const summary = decodeWorkSummary(item.summary, projectId);
    if (
      !sameUuid(gate.work_item_id, summary.work_item.id)
      || summary.readiness.unresolved_gate_count < 1
      || !summary.readiness.is_gated
    ) throw new Error("Mnemonic returned an incoherent human-attention item.");
    return { gate, summary } satisfies HumanAttentionItem;
  }, options.limit);
  return decoded as HumanAttentionPage;
}

export function decodeHumanGatePage(
  value: unknown,
  projectId: string,
  workItemId: string,
  options: { status?: "all" | HumanGateStatus; limit?: number } = {}
): HumanGatePage {
  if (!validUuid(projectId) || !validUuid(workItemId)) {
    throw new Error("The expected human-gate history scope is invalid.");
  }
  const expectedStatus = options.status === "all" ? undefined : options.status;
  return decodeCursorPage(value, (item) => decodeHumanGate(item, {
    projectId,
    workItemId,
    status: expectedStatus
  }), options.limit) as HumanGatePage;
}

export function humanAttentionSearchParams(input: {
  workItemId?: string;
  limit?: number;
  cursor?: string | null;
} = {}): URLSearchParams {
  const params = new URLSearchParams({ limit: String(input.limit ?? 30) });
  if (input.workItemId) params.set("work_item_id", input.workItemId);
  if (input.cursor) params.set("cursor", input.cursor);
  return params;
}

export function humanGateHistorySearchParams(input: {
  status?: "all" | HumanGateStatus;
  limit?: number;
  cursor?: string | null;
} = {}): URLSearchParams {
  const params = new URLSearchParams({
    status: input.status ?? "all",
    limit: String(input.limit ?? 30)
  });
  if (input.cursor) params.set("cursor", input.cursor);
  return params;
}

export function humanGatePath(projectId: string, workItemId: string, gateId?: string): string {
  const base = `/projects/${encodeURIComponent(projectId)}/work-items/${encodeURIComponent(workItemId)}/gates`;
  return gateId ? `${base}/${encodeURIComponent(gateId)}` : base;
}

function coherentReviewRelationship(
  context: Pick<WorkContext, "work_item">,
  value: unknown,
  expectedDirection: "incoming" | "outgoing" | "undirected",
  seenIds: Set<string>
): value is AdjacentRelationshipRead {
  const adjacent = objectValue(value);
  const relationship = objectValue(adjacent?.relationship);
  const counterpart = objectValue(adjacent?.counterpart);
  if (
    !adjacent
    || !relationship
    || !counterpart
    || !validUuid(context.work_item.id)
    || !validUuid(context.work_item.project_id)
    || !validUuid(relationship.id)
    || !validUuid(relationship.project_id)
    || !validUuid(relationship.source_work_item_id)
    || !validUuid(relationship.target_work_item_id)
    || !validUuid(adjacent.relative_to_work_item_id)
    || !validUuid(counterpart.id)
    || !sameUuid(relationship.project_id, context.work_item.project_id)
    || !sameUuid(adjacent.relative_to_work_item_id, context.work_item.id)
  ) return false;

  const sourceIsFocal = sameUuid(relationship.source_work_item_id, context.work_item.id);
  const targetIsFocal = sameUuid(relationship.target_work_item_id, context.work_item.id);
  if (sourceIsFocal === targetIsFocal) return false;
  const relationshipType = relationship.relationship_type;
  const actualDirection = relationshipType === "related"
    ? "undirected"
    : targetIsFocal ? "incoming" : "outgoing";
  const counterpartId = sourceIsFocal
    ? relationship.target_work_item_id
    : relationship.source_work_item_id;
  const normalizedId = relationship.id.toLowerCase();
  if (
    adjacent.direction !== expectedDirection
    || actualDirection !== expectedDirection
    || !sameUuid(counterpart.id, counterpartId)
    || seenIds.has(normalizedId)
  ) return false;
  seenIds.add(normalizedId);
  return true;
}

export function hasCompleteRelationshipReview(context: Pick<
  WorkContext,
  "work_item" | "incoming_relationships" | "outgoing_relationships"
    | "undirected_relationships" | "relationship_counts"
>): boolean {
  const incoming = context.incoming_relationships;
  const outgoing = context.outgoing_relationships;
  const undirected = context.undirected_relationships;
  const counts = context.relationship_counts;
  if (
    !Array.isArray(incoming)
    || !Array.isArray(outgoing)
    || !Array.isArray(undirected)
    || !counts
    || !finiteInteger(counts.incoming)
    || !finiteInteger(counts.outgoing)
    || !finiteInteger(counts.undirected)
    || !finiteInteger(counts.total)
    || counts.incoming !== incoming.length
    || counts.outgoing !== outgoing.length
    || counts.undirected !== undirected.length
    || counts.total !== incoming.length + outgoing.length + undirected.length
  ) return false;
  const seenIds = new Set<string>();
  return incoming.every((item) => (
    coherentReviewRelationship(context, item, "incoming", seenIds)
  )) && outgoing.every((item) => (
    coherentReviewRelationship(context, item, "outgoing", seenIds)
  )) && undirected.every((item) => (
    coherentReviewRelationship(context, item, "undirected", seenIds)
  ));
}

export function humanGateChangedLabels(gate: HumanGateRead): string[] {
  return [
    ...(gate.work_changed_since_request ? ["work fields"] : []),
    ...(gate.context_checkpoint_changed_since_request ? ["current context checkpoint"] : []),
    ...(gate.relationships_changed_since_request ? ["relationships"] : [])
  ];
}

export function humanGateCurrentDriftMessage(gate: HumanGateRead): string | null {
  if (gate.status !== "unresolved" || !gate.context_changed_since_request) return null;
  return `Current drift: ${humanGateChangedLabels(gate).join(", ")}.`;
}

export function humanGateOmissionSentence(
  kind: "unresolved" | "resolved",
  count: number
): string | null {
  if (count <= 0) return null;
  if (kind === "unresolved") {
    return `${count} additional unresolved question${count === 1 ? " is" : "s are"} omitted from bounded recall. Use the filtered attention queue.`;
  }
  return `${count} older resolved decision${count === 1 ? " is" : "s are"} omitted from bounded recall.`;
}

export function humanGateResolutionStatus(remaining: number): string {
  if (remaining === 0) return "Answer recorded. No unresolved questions remain.";
  return `Answer recorded. ${remaining} unresolved question${remaining === 1 ? " remains" : "s remain"}.`;
}
