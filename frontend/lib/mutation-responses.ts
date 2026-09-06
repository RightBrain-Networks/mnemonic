import { sameExternalReferences } from "./external-references.ts";
import { ApiError, detailMessage } from "./api.ts";
import type {
  Checkpoint,
  CompletionResult,
  DeletionResult,
  RelationshipCreationResult,
  RelationshipRemovalResult,
  HumanGateRead,
  WorkCreation,
  WorkItem,
  WorkUpdate,
  JobReportDismissalResult,
  JobReportFollowUpResult,
  WorkMergeResult,
  WorkMoveResult,
  WorkStatus
} from "@/lib/types";
import { decodeHumanGate } from "./human-gates.ts";
import { decodeWorkIdentityPointer, decodeWorkItem } from "./work-codecs.ts";
import { decodeCheckpoint } from "./checkpoint-codecs.ts";
import {
  decodeRelationship,
  expectedRelationship,
  relationshipIdentity
} from "./relationship-codecs.ts";
import {
  decodeMergeReviewRevision,
  sameHumanGateRevision,
  sameMergeReviewRevision,
  validHumanGateRevision,
  validMergeReviewRevision
} from "./revision-codecs.ts";
import { isDefinitiveProxyError } from "./proxy-policy.ts";
import {
  UUID_PATTERN,
  boundedText,
  compareUtcDateTimes,
  exactKeys,
  finiteInteger,
  jsonEqual,
  objectValue,
  sameNullableUuid,
  sameUuid,
  validUtcDateTime,
  validUuid,
  type JsonObject
} from "./wire-guards.ts";
import { decodeWorkEventForWork } from "./work-events.ts";
import type { WorkEventRead } from "@/lib/types";
import {
  completionEvidencePayloadMatchesInput,
  decodeCompletionEvidencePayload,
  normalizeCompletionEvidenceInput
} from "./completion-evidence.ts";

import { decodeReportDismissal, decodeReportFollowUp, matchCloseoutReport } from "./job-completion-reports.ts";
import { readBoundedJson } from "./bounded-json.ts";
import { decodeCodeReview, decodeReviewPolicy, decodeWorkFollowUp, decodeFollowUpAnswerResult, validFollowUpAnswer, validReviewHandoff, type FollowUpAnswerResult } from "./code-reviews.ts";
import type { MutationActor } from "./types.ts";

export const MUTATION_KINDS = [
  "create_work",
  "add_checkpoint",
  "append_event",
  "add_relationship",
  "update_work",
  "defer_work",
  "complete_work",
  "respond_to_work_follow_up",
  "delete_work",
  "move_work",
  "remove_relationship",
  "resolve_human_input",
  "merge_work",
  "dismiss_job_completion_report",
  "create_job_completion_report_follow_up"
] as const;

export type MutationKind = typeof MUTATION_KINDS[number];

export interface MutationResultByKind {
  create_work: WorkCreation;
  add_checkpoint: Checkpoint;
  append_event: WorkEventRead;
  add_relationship: RelationshipCreationResult;
  update_work: WorkUpdate;
  defer_work: WorkItem;
  complete_work: CompletionResult;
  respond_to_work_follow_up: FollowUpAnswerResult;
  delete_work: DeletionResult;
  move_work: WorkMoveResult;
  remove_relationship: RelationshipRemovalResult;
  resolve_human_input: HumanGateRead;
  merge_work: WorkMergeResult;
  dismiss_job_completion_report: JobReportDismissalResult;
  create_job_completion_report_follow_up: JobReportFollowUpResult;
}

export interface FrozenMutationRequest {
  readonly kind: MutationKind;
  readonly method: "POST" | "PATCH" | "DELETE";
  readonly path: string;
  readonly body: string;
  readonly operationId: string;
  readonly expectedSourceWorkItemId?: string;
  readonly expectedSourceWorkStatus?: WorkStatus;
}

export type MutationHttpOutcome<K extends MutationKind = MutationKind> =
  | { readonly type: "success"; readonly value: MutationResultByKind[K] }
  | { readonly type: "rejected"; readonly error: ApiError }
  | { readonly type: "safety_conflict"; readonly error: ApiError }
  | { readonly type: "unresolved"; readonly message: string };

const WORK_COMPLETION_RESPONSE_FIELDS = [
  "work_item", "checkpoint", "completion_evidence", "job_completion_report", "review_policy_decision", "code_review_request", "agent_follow_ups", "code_review_handoff"
] as const;

export const MUTATION_RESPONSE_DECODER_FIELDS = {
  "decodeMutationResult:complete_work": WORK_COMPLETION_RESPONSE_FIELDS
} as const;
const EXPECTED_STATUS: Record<MutationKind, number> = {
  create_work: 201,
  add_checkpoint: 201,
  append_event: 201,
  add_relationship: 200,
  update_work: 200,
  defer_work: 200,
  complete_work: 200,
  respond_to_work_follow_up: 200,
  delete_work: 200,
  move_work: 200,
  remove_relationship: 200,
  resolve_human_input: 200,
  merge_work: 201,
  dismiss_job_completion_report: 200,
  create_job_completion_report_follow_up: 201
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
    "gate_not_found", "job_completion_report_not_found", "code_review_not_found", "work_follow_up_not_found"
  ])],
  [409, new Set([
    "job_report_prompt_changed", "project_settings_changed",
    "work_follow_up_changed", "work_follow_up_superseded", "work_follow_up_origin_mismatch", "work_follow_up_already_answered",
    "code_review_not_requested", "code_review_superseded", "code_review_changed", "code_review_already_completed",
    "code_review_depth_forbidden", "code_review_remediation_disabled", "lease_purpose_mismatch",
    "code_review_obligation_outstanding", "code_review_provenance_merge_forbidden",
    "version_conflict",
    "invalid_status_transition",
    "work_not_pending",
    "completion_episode_unsealed",
    "closeout_report_unsealed",
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
    "work_move_same_project",
    "work_move_active_lease",
    "work_move_duplicate_membership",
    "work_move_review_history_conflict",
    "code_review_provenance_relationship_protected",
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
    "code_review_handoff_required", "code_review_handoff_not_applicable", "work_follow_up_answer_invalid", "code_review_scope_mismatch", "code_review_coverage_incomplete",
    "merge_secret_echo", "job_completion_report_required", "job_completion_report_not_applicable", "client_operation_id_required",
    "initial_status_must_be_pending", "job_report_secret_echo"
  ])],
  [503, new Set(["duplicate_graph_invalid"])]
]);
const DEFINITIVE_API_STRING_ERRORS = new Map<number, ReadonlySet<string>>([
  [401, new Set(["Valid bearer authentication is required"])]
]);


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

function sameMergeRevision(left: unknown, right: unknown): boolean {
  return validMergeReviewRevision(left)
    && validMergeReviewRevision(right)
    && sameMergeReviewRevision(left, right);
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
      !== decodeMergeReviewRevision(merge.reviewed_source_revision, "Mnemonic returned an invalid merge response.").work_version + 1
    || merge.resulting_destination_work_version
      !== decodeMergeReviewRevision(merge.reviewed_destination_revision, "Mnemonic returned an invalid merge response.").work_version + 1
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
      reviewed_source_revision: decodeMergeReviewRevision(merge.reviewed_source_revision, "Mnemonic returned an invalid merge response."),
      reviewed_destination_revision: decodeMergeReviewRevision(merge.reviewed_destination_revision, "Mnemonic returned an invalid merge response.")
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
      || !sameExternalReferences(workItem.external_references, body.external_references)
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
      relationship: decodeRelationship(
        result.relationship,
        result.created ? path.projectId : undefined,
        body,
        undefined,
        result.created
      ),
      created: result.created
    };
  } else if (request.kind === "update_work") {
    const path = parsePath(request.path, "");
    if (!path?.workItemId) throw new Error("The frozen mutation request is invalid.");
    const raw = objectValue(value);
    if (!raw) throw new Error("Mnemonic returned an invalid work update.");
    const { job_completion_report: report, ...workFields } = raw;
    if (Object.hasOwn(body, "job_completion_report") !== Object.hasOwn(raw, "job_completion_report")) {
      throw new Error("Mnemonic returned an incoherent closeout report.");
    }
    const workItem = decodeWorkItem(workFields, path.projectId, path.workItemId);
    if (
      workItem.version !== Number(body.expected_version) + 1
      || (Object.hasOwn(body, "external_references") && !sameExternalReferences(workItem.external_references, body.external_references))
      || (body.title !== undefined && workItem.title !== String(body.title).trim())
      || (body.summary !== undefined && workItem.summary !== String(body.summary).trim())
      || (body.priority !== undefined && workItem.priority !== body.priority)
      || (body.status !== undefined && workItem.status !== body.status)
    ) throw new Error("Mnemonic returned an incoherent mutation response.");
    decoded = Object.hasOwn(body, "job_completion_report")
      ? { ...workItem, job_completion_report: matchCloseoutReport(report, path.projectId, workItem, body.job_completion_report, body.actor, null) }
      : workItem;
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
    let expectedEvidence;
    try {
      expectedEvidence = normalizeCompletionEvidenceInput(body.completion_evidence);
    } catch {
      throw new Error("The frozen mutation request is invalid.");
    }
    if (
      !path?.workItemId
      || !result
      || !exactKeys(
        result,
        ["work_item", "checkpoint", ...(expectedEvidence ? ["completion_evidence"] : []),
          ...(Object.hasOwn(body, "job_completion_report") ? ["job_completion_report"] : []),
          ...["review_policy_decision", "code_review_request", "agent_follow_ups", "code_review_handoff"].filter((key) => Object.hasOwn(result, key))]
      )
    ) {
      throw new Error("Mnemonic returned an invalid mutation response.");
    }
    const workItem = decodeWorkItem(result.work_item, path.projectId, path.workItemId);
    const checkpoint = decodeCheckpoint(result.checkpoint, path.workItemId, "completion", body.checkpoint);
    if (workItem.status !== "done" || workItem.version !== Number(body.expected_version) + 1) {
      throw new Error("Mnemonic returned an incoherent mutation response.");
    }
    if (expectedEvidence) {
      const evidence = decodeCompletionEvidencePayload(
        result.completion_evidence,
        path.workItemId,
        checkpoint.id,
        checkpoint.created_at
      );
      if (!completionEvidencePayloadMatchesInput(evidence, expectedEvidence)) {
        throw new Error("Mnemonic returned an incoherent mutation response.");
      }
      decoded = { work_item: workItem, checkpoint, completion_evidence: evidence };
    } else {
      decoded = { work_item: workItem, checkpoint };
    }
    if (Object.hasOwn(body, "job_completion_report")) {
      (decoded as CompletionResult).job_completion_report = matchCloseoutReport(
        result.job_completion_report, path.projectId, workItem, body.job_completion_report,
        body.checkpoint, checkpoint.id
      );
    }
    if (Object.hasOwn(result, "review_policy_decision")) {
      const policy = decodeReviewPolicy(result.review_policy_decision, path.projectId, path.workItemId);
      if (!sameUuid(policy.completion_checkpoint_id, checkpoint.id) || policy.priority_at_closeout !== workItem.priority
        || policy.settings_revision !== objectValue(body.job_completion_report)?.prompt_revision) throw new Error("Mnemonic returned an incoherent review policy.");
      const review = result.code_review_request === undefined ? undefined : decodeCodeReview(result.code_review_request, path.projectId, path.workItemId);
      const followUps = result.agent_follow_ups === undefined ? undefined : Array.isArray(result.agent_follow_ups)
        ? result.agent_follow_ups.map((entry) => decodeWorkFollowUp(entry, path.projectId, path.workItemId)) : null;
      if ((policy.decision === "mandatory") !== Boolean(review)
        || (policy.decision === "ask_recommendation") !== Boolean(followUps)
        || followUps && (followUps.length !== 1 || !sameUuid(followUps[0]!.kind_data.policy_decision_id, policy.id)
          || !sameUuid(followUps[0]!.completion_checkpoint_id, checkpoint.id) || followUps[0]!.state !== "pending")
        || review && (!sameUuid(review.policy_decision_id, policy.id) || !sameUuid(review.completion_checkpoint_id, checkpoint.id)
          || review.state !== "requested" || review.request_reason !== "mandatory" || !Object.hasOwn(body, "code_review_handoff"))
        || Boolean(review) !== Object.hasOwn(result, "code_review_handoff")
        || review && (!validReviewHandoff(result.code_review_handoff) || !jsonEqual(result.code_review_handoff, body.code_review_handoff))) {
        throw new Error("Mnemonic returned incoherent completion review resources.");
      }
      Object.assign(decoded as CompletionResult, { review_policy_decision: policy, ...(review ? { code_review_request: review, code_review_handoff: result.code_review_handoff } : {}), ...(followUps ? { agent_follow_ups: followUps } : {}) });
    } else if (result.code_review_request !== undefined || result.agent_follow_ups !== undefined || body.code_review_handoff !== undefined) {
      throw new Error("Mnemonic omitted the review policy.");
    }
  } else if (request.kind === "respond_to_work_follow_up") {
    const uuid = UUID_PATTERN.source.slice(1, -1);
    const match = new RegExp(`^/projects/(${uuid})/work-items/(${uuid})/agent-follow-ups/(${uuid})/answer$`).exec(request.path);
    if (!match || !finiteInteger(body.expected_follow_up_version, 1) || !validFollowUpAnswer(body.answer) || !objectValue(body.actor)) throw new Error("The frozen follow-up answer is invalid.");
    decoded = decodeFollowUpAnswerResult(value, match[1]!, match[2]!, match[3]!, body.expected_follow_up_version, body.answer, body.actor as unknown as MutationActor);
  } else if (request.kind === "dismiss_job_completion_report" || request.kind === "create_job_completion_report_follow_up") {
    const uuid = UUID_PATTERN.source.slice(1, -1);
    const suffix = request.kind === "dismiss_job_completion_report" ? "dismiss" : "follow-ups";
    const match = new RegExp(`^/projects/(${uuid})/job-completion-reports/(${uuid})/${suffix}$`).exec(request.path);
    const result = objectValue(value);
    const actor = objectValue(body.actor);
    if (!match || !result || !actor) throw new Error("The frozen report action is invalid.");
    const projectId = match[1]!;
    const reportId = match[2]!;
    if (request.kind === "dismiss_job_completion_report") {
      if (!exactKeys(result, ["project_id", "report_id", "dismissed", "human_dismissal"])
        || !sameUuid(result.project_id, projectId) || !sameUuid(result.report_id, reportId)
        || typeof result.dismissed !== "boolean") throw new Error("Mnemonic returned an invalid dismissal.");
      const dismissal = decodeReportDismissal(result.human_dismissal, projectId, reportId);
      if (result.dismissed && (dismissal.actor_client !== actor.actor_client
        || dismissal.actor_session_id !== actor.actor_session_id
        || dismissal.actor_model !== (actor.actor_model ?? null))) throw new Error("Mnemonic returned an incoherent dismissal.");
      decoded = { project_id: projectId, report_id: reportId, dismissed: result.dismissed, human_dismissal: dismissal };
    } else {
      if (!exactKeys(result, ["work_item", "initial_checkpoint", "follow_up"])) throw new Error("Mnemonic returned an invalid follow-up.");
      const work = decodeWorkItem(result.work_item, projectId);
      const checkpoint = decodeCheckpoint(result.initial_checkpoint, work.id, "context", body.initial_checkpoint);
      const link = decodeReportFollowUp(result.follow_up, projectId, reportId);
      if (!validUuid(request.expectedSourceWorkItemId) || !sameUuid(link.source_work_item_id, request.expectedSourceWorkItemId)
        || !sameUuid(link.follow_up_work_item_id, work.id) || !sameUuid(work.initial_checkpoint_id, checkpoint.id)
        || work.version !== 1 || work.status !== "pending" || work.title !== String(body.title).trim()
        || work.summary !== String(body.summary).trim() || work.priority !== (body.priority ?? 0)
        || link.actor_client !== actor.actor_client || link.actor_session_id !== actor.actor_session_id
        || link.actor_model !== (actor.actor_model ?? null) || checkpoint.source_client !== link.actor_client
        || checkpoint.source_session_id !== link.actor_session_id || checkpoint.source_model !== link.actor_model
      ) throw new Error("Mnemonic returned an incoherent follow-up.");
      decoded = { work_item: work, initial_checkpoint: checkpoint, follow_up: link };
    }
  } else if (request.kind === "move_work") {
    const path = parsePath(request.path, "/move");
    const result = objectValue(value);
    if (
      !path?.workItemId
      || !result
      || !exactKeys(body, [
        "target_project_id", "expected_version", "actor", "client_operation_id"
      ])
      || !validUuid(body.target_project_id)
      || sameUuid(body.target_project_id, path.projectId)
      || !finiteInteger(body.expected_version, 1)
      || !exactKeys(result, [
        "source_project_id", "target_project_id", "preserved_status", "work_item"
      ])
      || !sameUuid(result.source_project_id, path.projectId)
      || !sameUuid(result.target_project_id, body.target_project_id)
      || request.expectedSourceWorkStatus === undefined
      || result.preserved_status !== request.expectedSourceWorkStatus
    ) throw new Error("Mnemonic returned an invalid move response.");
    const workItem = decodeWorkItem(
      result.work_item,
      body.target_project_id,
      path.workItemId,
      "Mnemonic returned an invalid move response."
    );
    if (
      workItem.version !== Number(body.expected_version) + 1
      || workItem.status !== result.preserved_status
    ) throw new Error("Mnemonic returned an incoherent move response.");
    decoded = {
      source_project_id: path.projectId,
      target_project_id: workItem.project_id,
      preserved_status: workItem.status,
      work_item: workItem
    };
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
      || !validHumanGateRevision(reviewed)
      || !sameHumanGateRevision(
        gate.resolved_context_revision,
        reviewed
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
    value = await readBoundedJson(response, request.kind === "respond_to_work_follow_up" ? 1_048_576 : 3_145_728);
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
