import type {
  AdjacentRelationshipRead,
  CanonicalWorkProjection,
  Checkpoint,
  DuplicateMergeEligibility,
  DuplicateScope,
  MergeReviewRevision,
  Page,
  RelationshipCounts,
  WorkContext,
  WorkIdentityPointer,
  WorkItemDetailRead,
  WorkMergeInput,
  WorkSearchHit
} from "@/lib/types";
import { decodeHumanGate } from "./human-gates.ts";
import { decodeReadiness } from "./readiness-codecs.ts";
import { decodeWorkIdentityPointer, decodeWorkItem, decodeWorkSummary } from "./work-codecs.ts";
import { decodeCheckpoint } from "./checkpoint-codecs.ts";
import { decodeRelationship } from "./relationship-codecs.ts";
import { decodeMergeReviewRevision } from "./revision-codecs.ts";
import { decodeWorkEventForWork } from "./work-events.ts";
import {
  boundedText,
  compareUtcDateTimes,
  exactKeys,
  finiteInteger,
  objectValue,
  sameUuid,
  validUuid
} from "./wire-guards.ts";

const PAGE_FIELDS = ["items", "total", "limit", "offset"] as const;
const SEARCH_HIT_FIELDS = ["summary", "matched_member"] as const;
const PROJECTION_FIELDS = [
  "is_duplicate",
  "direct_destination",
  "canonical_work_item",
  "path",
  "duplicate_member_count"
] as const;
const DETAIL_FIELDS = ["work_item", "canonical"] as const;
const COUNTS_FIELDS = ["incoming", "outgoing", "undirected", "total"] as const;
const ELIGIBILITY_FIELDS = [
  "incident_blocks_count",
  "incident_parent_child_count",
  "has_unresolved_gate",
  "source_lease_state"
] as const;
const ADJACENT_FIELDS = [
  "relationship", "relative_to_work_item_id", "direction", "counterpart"
] as const;
const WORK_POINTER_FIELDS = ["id", "title", "status", "readiness"] as const;
const CONTEXT_FIELDS = [
  "work_item",
  "merge_review_revision",
  "canonical",
  "duplicate_members",
  "duplicate_member_total",
  "omitted_duplicate_member_count",
  "initial_checkpoint",
  "current_context",
  "current_context_is_initial",
  "recent_checkpoints",
  "checkpoint_total",
  "omitted_checkpoint_count",
  "readiness",
  "unresolved_gates",
  "unresolved_gate_total",
  "omitted_unresolved_gate_count",
  "recent_resolved_gates",
  "resolved_gate_total",
  "omitted_resolved_gate_count",
  "incoming_relationships",
  "outgoing_relationships",
  "undirected_relationships",
  "relationship_counts",
  "omitted_relationship_counts",
  "duplicate_merge_eligibility",
  "recent_events",
  "event_total",
  "omitted_event_count",
  "pre_phase5_history_may_be_incomplete"
] as const;

export const DUPLICATE_HANDLING_DECODER_FIELDS = {
  decodeCanonicalWorkProjection: PROJECTION_FIELDS,
  decodeWorkContext: CONTEXT_FIELDS,
  decodeWorkItemDetail: DETAIL_FIELDS,
  "decodeWorkSearchPage:item": SEARCH_HIT_FIELDS,
  decodeWorkSearchPage: PAGE_FIELDS
} as const;

function pointerEqual(left: WorkIdentityPointer, right: WorkIdentityPointer): boolean {
  return sameUuid(left.id, right.id)
    && left.title === right.title
    && left.status === right.status;
}


export function duplicateMergeEligibilityReasons(
  eligibility: DuplicateMergeEligibility
): string[] {
  return [
    ...(eligibility.incident_blocks_count > 0
      ? [`Reconcile ${eligibility.incident_blocks_count} incident blocker relationship${eligibility.incident_blocks_count === 1 ? "" : "s"}.`]
      : []),
    ...(eligibility.incident_parent_child_count > 0
      ? [`Reconcile ${eligibility.incident_parent_child_count} incident parent/child relationship${eligibility.incident_parent_child_count === 1 ? "" : "s"}.`]
      : []),
    ...(eligibility.has_unresolved_gate
      ? ["Resolve the source’s unresolved human question before rereading both contexts."]
      : []),
    ...(eligibility.source_lease_state === "active"
      ? ["Release the source’s active lease, or wait for it to expire, before merging in the browser."]
      : [])
  ];
}

export function decodeCanonicalWorkProjection(
  value: unknown,
  requested?: { id: string; title: string; status: string }
): CanonicalWorkProjection {
  const projection = objectValue(value);
  if (
    !projection
    || !exactKeys(projection, PROJECTION_FIELDS)
    || typeof projection.is_duplicate !== "boolean"
    || !Array.isArray(projection.path)
    || projection.path.length > 50
    || !finiteInteger(projection.duplicate_member_count)
  ) throw new Error("Mnemonic returned an invalid canonical work projection.");
  const directDestination = projection.direct_destination === null
    ? null
    : decodeWorkIdentityPointer(projection.direct_destination);
  const canonicalWorkItem = decodeWorkIdentityPointer(projection.canonical_work_item);
  const path = projection.path.map(decodeWorkIdentityPointer);
  const ids = path.map((item) => item.id.toLowerCase());
  if (new Set(ids).size !== ids.length) {
    throw new Error("Mnemonic returned an invalid canonical work path.");
  }
  if (projection.is_duplicate) {
    if (
      directDestination === null
      || path.length === 0
      || !pointerEqual(path[0]!, directDestination)
      || !pointerEqual(path.at(-1)!, canonicalWorkItem)
      || projection.duplicate_member_count < path.length
      || requested !== undefined && (
        path.some((item) => sameUuid(item.id, requested.id))
        || sameUuid(canonicalWorkItem.id, requested.id)
      )
    ) throw new Error("Mnemonic returned an incoherent canonical work projection.");
  } else if (
    directDestination !== null
    || path.length !== 0
    || requested !== undefined && (
      !sameUuid(canonicalWorkItem.id, requested.id)
      || canonicalWorkItem.title !== requested.title
      || canonicalWorkItem.status !== requested.status
    )
  ) {
    throw new Error("Mnemonic returned an incoherent canonical work projection.");
  }
  return {
    is_duplicate: projection.is_duplicate,
    direct_destination: directDestination,
    canonical_work_item: canonicalWorkItem,
    path,
    duplicate_member_count: projection.duplicate_member_count
  };
}

export function decodeWorkItemDetail(
  value: unknown,
  projectId: string,
  workItemId: string
): WorkItemDetailRead {
  const detail = objectValue(value);
  if (!detail || !exactKeys(detail, DETAIL_FIELDS)) {
    throw new Error("Mnemonic returned an invalid work-item detail.");
  }
  const workItem = decodeWorkItem(
    detail.work_item,
    projectId,
    workItemId,
    "Mnemonic returned an invalid work-item detail."
  );
  return {
    work_item: workItem,
    canonical: decodeCanonicalWorkProjection(detail.canonical, workItem)
  };
}

export function decodeWorkSearchPage(
  value: unknown,
  projectId: string,
  options: {
    duplicateScope?: DuplicateScope;
    canonicalWorkItemId?: string;
    query?: string;
    expectedLimit?: number;
    expectedOffset?: number;
  } = {}
): Page<WorkSearchHit> {
  const page = objectValue(value);
  const duplicateScope = options.duplicateScope ?? "canonical";
  if (
    !page
    || !exactKeys(page, PAGE_FIELDS)
    || !Array.isArray(page.items)
    || !finiteInteger(page.total)
    || !finiteInteger(page.limit, 1, 100)
    || !finiteInteger(page.offset)
    || page.items.length > Number(page.limit)
    || page.items.length > Number(page.total)
    || page.items.length > 0 && Number(page.offset) + page.items.length > Number(page.total)
    || options.expectedLimit !== undefined && page.limit !== options.expectedLimit
    || options.expectedOffset !== undefined && page.offset !== options.expectedOffset
    || !["canonical", "aliases", "all"].includes(duplicateScope)
    || options.canonicalWorkItemId !== undefined && (
      duplicateScope === "canonical" || !validUuid(options.canonicalWorkItemId)
    )
  ) throw new Error("Mnemonic returned an invalid work search page.");
  const blankQuery = (options.query ?? "").trim().length === 0;
  const items = page.items.map((valueItem) => {
    const item = objectValue(valueItem);
    if (!item || !exactKeys(item, SEARCH_HIT_FIELDS)) {
      throw new Error("Mnemonic returned an invalid work search hit.");
    }
    const summary = decodeWorkSummary(item.summary, projectId);
    const matchedMember = decodeWorkIdentityPointer(item.matched_member);
    if (
      duplicateScope === "canonical" && summary.readiness.is_duplicate
      || duplicateScope === "aliases" && !summary.readiness.is_duplicate
      || options.canonicalWorkItemId !== undefined
        && !sameUuid(summary.readiness.canonical_work_item_id, options.canonicalWorkItemId)
      || (blankQuery || duplicateScope !== "canonical") && !(
        sameUuid(matchedMember.id, summary.work_item.id)
        && matchedMember.title === summary.work_item.title
        && matchedMember.status === summary.work_item.status
      )
    ) throw new Error("Mnemonic returned an incoherent work search hit.");
    return { summary, matched_member: matchedMember };
  });
  const itemIds = items.map((item) => item.summary.work_item.id.toLowerCase());
  if (new Set(itemIds).size !== itemIds.length) {
    throw new Error("Mnemonic returned repeated work search hits.");
  }
  return {
    items,
    total: page.total,
    limit: page.limit,
    offset: page.offset
  };
}

function decodeCounts(value: unknown): RelationshipCounts {
  const counts = objectValue(value);
  if (
    !counts
    || !exactKeys(counts, COUNTS_FIELDS)
    || !finiteInteger(counts.incoming)
    || !finiteInteger(counts.outgoing)
    || !finiteInteger(counts.undirected)
    || !finiteInteger(counts.total)
    || counts.total !== counts.incoming + counts.outgoing + counts.undirected
  ) throw new Error("Mnemonic returned invalid relationship counts.");
  return counts as unknown as RelationshipCounts;
}

function decodeEligibility(value: unknown): DuplicateMergeEligibility {
  const eligibility = objectValue(value);
  if (
    !eligibility
    || !exactKeys(eligibility, ELIGIBILITY_FIELDS)
    || !finiteInteger(eligibility.incident_blocks_count)
    || !finiteInteger(eligibility.incident_parent_child_count)
    || typeof eligibility.has_unresolved_gate !== "boolean"
    || !["none", "expired", "active"].includes(String(eligibility.source_lease_state))
  ) throw new Error("Mnemonic returned invalid duplicate-merge eligibility.");
  return eligibility as unknown as DuplicateMergeEligibility;
}

function decodeAdjacent(
  value: unknown,
  projectId: string,
  workItemId: string,
  direction: "incoming" | "outgoing" | "undirected"
): AdjacentRelationshipRead {
  const adjacent = objectValue(value);
  const counterpart = objectValue(adjacent?.counterpart);
  if (
    !adjacent
    || !exactKeys(adjacent, ADJACENT_FIELDS)
    || !sameUuid(adjacent.relative_to_work_item_id, workItemId)
    || adjacent.direction !== direction
    || !counterpart
    || !exactKeys(counterpart, WORK_POINTER_FIELDS)
  ) throw new Error("Mnemonic returned an invalid adjacent relationship.");
  const relationship = decodeRelationship(adjacent.relationship, projectId);
  const pointer = decodeWorkIdentityPointer({
    id: counterpart.id,
    title: counterpart.title,
    status: counterpart.status
  });
  const sourceIsFocal = sameUuid(relationship.source_work_item_id, workItemId);
  const targetIsFocal = sameUuid(relationship.target_work_item_id, workItemId);
  const actualDirection = relationship.relationship_type === "related"
    ? "undirected"
    : targetIsFocal ? "incoming" : "outgoing";
  const expectedCounterpart = sourceIsFocal
    ? relationship.target_work_item_id
    : relationship.source_work_item_id;
  if (
    sourceIsFocal === targetIsFocal
    || direction !== actualDirection
    || !sameUuid(pointer.id, expectedCounterpart)
  ) throw new Error("Mnemonic returned an incoherent adjacent relationship.");
  return {
    relationship,
    relative_to_work_item_id: workItemId,
    direction,
    counterpart: {
      ...pointer,
      readiness: decodeReadiness(counterpart.readiness, pointer.status, pointer.id)
    }
  };
}

function checkpointIdentity(checkpoint: Checkpoint): string {
  return checkpoint.id.toLowerCase();
}

export function decodeWorkContext(
  value: unknown,
  projectId: string,
  workItemId: string
): WorkContext {
  const context = objectValue(value);
  if (!context || !exactKeys(context, CONTEXT_FIELDS)) {
    throw new Error("Mnemonic returned an invalid work context.");
  }
  const workItem = decodeWorkItem(
    context.work_item,
    projectId,
    workItemId,
    "Mnemonic returned an invalid work context."
  );
  const canonical = decodeCanonicalWorkProjection(context.canonical, workItem);
  const readiness = decodeReadiness(context.readiness, workItem.status, workItem.id);
  const revision = decodeMergeReviewRevision(context.merge_review_revision);
  const initialCheckpoint = decodeCheckpoint(
    context.initial_checkpoint,
    workItem.id,
    "context"
  );
  const currentIsInitial = context.current_context_is_initial;
  if (typeof currentIsInitial !== "boolean") {
    throw new Error("Mnemonic returned an invalid work context.");
  }
  const currentCheckpoint = context.current_context === null
    ? null
    : decodeCheckpoint(context.current_context, workItem.id, "context");
  const effectiveContextId = currentIsInitial ? initialCheckpoint.id : currentCheckpoint?.id;
  if (
    !sameUuid(initialCheckpoint.id, workItem.initial_checkpoint_id)
    || (currentIsInitial ? currentCheckpoint !== null : currentCheckpoint === null)
    || !effectiveContextId
    || revision.work_version !== workItem.version
    || !sameUuid(revision.context_checkpoint_id, effectiveContextId)
    || readiness.is_duplicate !== canonical.is_duplicate
    || !sameUuid(readiness.canonical_work_item_id, canonical.canonical_work_item.id)
  ) throw new Error("Mnemonic returned an incoherent work context.");

  if (!Array.isArray(context.recent_checkpoints) || context.recent_checkpoints.length > 20) {
    throw new Error("Mnemonic returned an invalid checkpoint slice.");
  }
  const recentCheckpoints = context.recent_checkpoints.map((entry) => (
    decodeCheckpoint(entry, workItem.id)
  ));
  const checkpointIds = new Set([
    checkpointIdentity(initialCheckpoint),
    ...(currentCheckpoint ? [checkpointIdentity(currentCheckpoint)] : [])
  ]);
  for (const checkpoint of recentCheckpoints) {
    const id = checkpointIdentity(checkpoint);
    if (checkpointIds.has(id)) throw new Error("Mnemonic returned a repeated checkpoint.");
    checkpointIds.add(id);
  }
  if (
    !finiteInteger(context.checkpoint_total, 1)
    || !finiteInteger(context.omitted_checkpoint_count)
    || context.checkpoint_total !== checkpointIds.size + context.omitted_checkpoint_count
  ) throw new Error("Mnemonic returned incoherent checkpoint counts.");

  if (!Array.isArray(context.duplicate_members) || context.duplicate_members.length > 20) {
    throw new Error("Mnemonic returned an invalid duplicate member slice.");
  }
  const duplicateMembers = context.duplicate_members.map(decodeWorkIdentityPointer);
  const memberIds = duplicateMembers.map((member) => member.id.toLowerCase());
  if (
    new Set(memberIds).size !== memberIds.length
    || duplicateMembers.some((member) => sameUuid(member.id, canonical.canonical_work_item.id))
    || canonical.is_duplicate && !pointerEqual(duplicateMembers[0]!, {
      id: workItem.id,
      title: workItem.title,
      status: workItem.status
    })
    || !finiteInteger(context.duplicate_member_total)
    || !finiteInteger(context.omitted_duplicate_member_count)
    || context.duplicate_member_total !== canonical.duplicate_member_count
    || context.omitted_duplicate_member_count
      !== context.duplicate_member_total - duplicateMembers.length
  ) throw new Error("Mnemonic returned an incoherent duplicate member slice.");

  const relationshipCounts = decodeCounts(context.relationship_counts);
  const omittedRelationshipCounts = decodeCounts(context.omitted_relationship_counts);
  const relationshipSlices = ([
    ["incoming", context.incoming_relationships],
    ["outgoing", context.outgoing_relationships],
    ["undirected", context.undirected_relationships]
  ] as const).map(([direction, entries]) => {
    if (!Array.isArray(entries) || entries.length > 100) {
      throw new Error("Mnemonic returned an invalid relationship slice.");
    }
    const decoded = entries.map((entry) => (
      decodeAdjacent(entry, projectId, workItem.id, direction)
    ));
    if (
      decoded.some((entry, index) => {
        if (index === 0) return false;
        const previous = decoded[index - 1]!.relationship;
        const current = entry.relationship;
        const timestampOrder = compareUtcDateTimes(previous.created_at, current.created_at);
        return timestampOrder > 0 || timestampOrder === 0
          && previous.id.toLowerCase() >= current.id.toLowerCase();
      })
      ||
      omittedRelationshipCounts[direction]
        !== relationshipCounts[direction] - decoded.length
    ) throw new Error("Mnemonic returned incoherent relationship omissions.");
    return decoded;
  });
  const [incomingRelationships, outgoingRelationships, undirectedRelationships] = relationshipSlices;
  const relationshipIds = [
    ...incomingRelationships,
    ...outgoingRelationships,
    ...undirectedRelationships
  ].map((entry) => entry.relationship.id.toLowerCase());
  if (
    new Set(relationshipIds).size !== relationshipIds.length
    || omittedRelationshipCounts.total !== relationshipCounts.total - relationshipIds.length
  ) throw new Error("Mnemonic returned incoherent relationship slices.");

  const eligibility = decodeEligibility(context.duplicate_merge_eligibility);
  const expectedLeaseState = readiness.has_active_lease
    ? "active"
    : readiness.has_dropped_lease ? "expired" : "none";
  if (
    eligibility.source_lease_state !== expectedLeaseState
    || eligibility.has_unresolved_gate !== (readiness.unresolved_gate_count > 0)
  ) throw new Error("Mnemonic returned incoherent duplicate-merge eligibility.");
  if (omittedRelationshipCounts.total === 0) {
    const allRelationships = [
      ...incomingRelationships,
      ...outgoingRelationships,
      ...undirectedRelationships
    ];
    if (
      eligibility.incident_blocks_count !== allRelationships.filter(
        (item) => item.relationship.relationship_type === "blocks"
      ).length
      || eligibility.incident_parent_child_count !== allRelationships.filter(
        (item) => item.relationship.relationship_type === "parent-child"
      ).length
    ) throw new Error("Mnemonic returned incoherent structural merge counts.");
  }

  if (!Array.isArray(context.recent_events) || context.recent_events.length > 20) {
    throw new Error("Mnemonic returned an invalid event slice.");
  }
  const recentEvents = context.recent_events.map((entry) => (
    decodeWorkEventForWork(entry, projectId, workItem.id)
  ));
  if (
    new Set(recentEvents.map((event) => event.id)).size !== recentEvents.length
    || recentEvents.some((event, index) => {
      if (index === 0) return false;
      const previous = recentEvents[index - 1]!;
      const timestampOrder = compareUtcDateTimes(previous.created_at, event.created_at);
      return timestampOrder > 0 || timestampOrder === 0 && previous.id >= event.id;
    })
    || !finiteInteger(context.event_total, 1)
    || !finiteInteger(context.omitted_event_count)
    || context.event_total !== recentEvents.length + context.omitted_event_count
    || revision.work_event_count !== context.event_total
    || typeof context.pre_phase5_history_may_be_incomplete !== "boolean"
  ) throw new Error("Mnemonic returned an incoherent event slice.");

  const decodeGateSlice = (entry: unknown, status: "unresolved" | "resolved") => (
    decodeHumanGate(entry, { projectId, workItemId, status })
  );
  if (
    !Array.isArray(context.unresolved_gates)
    || context.unresolved_gates.length > 20
    || !Array.isArray(context.recent_resolved_gates)
    || context.recent_resolved_gates.length > 20
  ) throw new Error("Mnemonic returned an invalid human-gate slice.");
  const unresolvedGates = context.unresolved_gates.map((entry) => (
    decodeGateSlice(entry, "unresolved")
  ));
  const recentResolvedGates = context.recent_resolved_gates.map((entry) => (
    decodeGateSlice(entry, "resolved")
  ));
  const gateIds = [...unresolvedGates, ...recentResolvedGates].map((gate) => gate.id.toLowerCase());
  const gateRevisions = [...unresolvedGates, ...recentResolvedGates].map(
    (gate) => gate.current_context_revision
  );
  if (
    new Set(gateIds).size !== gateIds.length
    || gateRevisions.some((gateRevision, index) => (
      gateRevision.work_version !== workItem.version
      || !sameUuid(gateRevision.context_checkpoint_id, effectiveContextId)
      || index > 0
        && gateRevision.relationship_event_count
          !== gateRevisions[0]!.relationship_event_count
    ))
    || !finiteInteger(context.unresolved_gate_total)
    || !finiteInteger(context.omitted_unresolved_gate_count)
    || context.unresolved_gate_total !== readiness.unresolved_gate_count
    || context.omitted_unresolved_gate_count
      !== context.unresolved_gate_total - unresolvedGates.length
    || !finiteInteger(context.resolved_gate_total)
    || !finiteInteger(context.omitted_resolved_gate_count)
    || context.omitted_resolved_gate_count
      !== context.resolved_gate_total - recentResolvedGates.length
  ) throw new Error("Mnemonic returned incoherent human-gate counts.");

  return {
    work_item: workItem,
    merge_review_revision: revision,
    canonical,
    duplicate_members: duplicateMembers,
    duplicate_member_total: context.duplicate_member_total,
    omitted_duplicate_member_count: context.omitted_duplicate_member_count,
    initial_checkpoint: initialCheckpoint,
    current_context: currentCheckpoint,
    current_context_is_initial: currentIsInitial,
    recent_checkpoints: recentCheckpoints,
    checkpoint_total: context.checkpoint_total,
    omitted_checkpoint_count: context.omitted_checkpoint_count,
    readiness,
    unresolved_gates: unresolvedGates,
    unresolved_gate_total: context.unresolved_gate_total,
    omitted_unresolved_gate_count: context.omitted_unresolved_gate_count,
    recent_resolved_gates: recentResolvedGates,
    resolved_gate_total: context.resolved_gate_total,
    omitted_resolved_gate_count: context.omitted_resolved_gate_count,
    incoming_relationships: incomingRelationships,
    outgoing_relationships: outgoingRelationships,
    undirected_relationships: undirectedRelationships,
    relationship_counts: relationshipCounts,
    omitted_relationship_counts: omittedRelationshipCounts,
    duplicate_merge_eligibility: eligibility,
    recent_events: recentEvents,
    event_total: context.event_total,
    omitted_event_count: context.omitted_event_count,
    pre_phase5_history_may_be_incomplete: context.pre_phase5_history_may_be_incomplete
  };
}

export function dashboardMergeInput(
  destinationWorkItemId: string,
  sourceRevision: MergeReviewRevision,
  destinationRevision: MergeReviewRevision,
  rationale: string,
  sessionId: string
): WorkMergeInput {
  const reviewedSourceRevision = decodeMergeReviewRevision(sourceRevision);
  const reviewedDestinationRevision = decodeMergeReviewRevision(destinationRevision);
  if (
    !validUuid(destinationWorkItemId)
    || !boundedText(rationale, 4_000)
    || !boundedText(sessionId, 200)
  ) throw new Error("The merge review is incomplete.");
  return {
    destination_work_item_id: destinationWorkItemId,
    reviewed_source_revision: { ...reviewedSourceRevision },
    reviewed_destination_revision: { ...reviewedDestinationRevision },
    rationale,
    merged_by_client: "dashboard",
    merged_by_session_id: sessionId,
    merged_by_model: null
  };
}

export function mergeWorkPath(projectId: string, sourceWorkItemId: string): string {
  return `/projects/${encodeURIComponent(projectId)}/work-items/${encodeURIComponent(sourceWorkItemId)}/merge`;
}
