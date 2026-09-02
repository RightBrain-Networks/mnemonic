export type WorkStatus = "pending" | "deferred" | "done" | "wont-do" | "promoted";
export type EventWorkStatus = "open" | WorkStatus;
export type EventCreateWorkStatus = Exclude<EventWorkStatus, "done">;
export type MutableWorkStatus = "pending" | "wont-do" | "promoted";
export type StatusFilter = WorkStatus | "active" | "dropped" | "all";
export type WorkSort = "updated" | "created" | "priority";
export type CheckpointKind = "context" | "progress" | "completion";
export type MigrationOrigin = "legacy-handoff-snapshot" | "legacy-comment" | null;

export interface Project {
  id: string;
  name: string;
  slug: string;
  description: string;
  repository_url: string | null;
  created_at: string;
  updated_at: string;
}

export interface ProjectSettings {
  project_id: string;
  recall_pointer_template: string | null;
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface WorkItem {
  id: string;
  project_id: string;
  title: string;
  summary: string;
  status: WorkStatus;
  priority: number;
  initial_checkpoint_id: string;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface CheckpointPointer {
  id: string;
  work_item_id: string;
  kind: CheckpointKind;
  source_client: string;
  source_session_id: string;
  source_model: string | null;
  repository_branch: string | null;
  verified_against: string | null;
  tags: string[];
  migration_origin: MigrationOrigin;
  legacy_record_id: string | null;
  created_at: string;
}

export interface Checkpoint extends CheckpointPointer {
  prompt: string;
  source_session_url: string | null;
  source_metadata: Record<string, unknown>;
}

export interface LeasePublic {
  holder_client: string;
  holder_session_id: string;
  acquired_at: string;
  renewed_at: string;
  expires_at: string;
}

export interface Readiness {
  lifecycle_status: WorkStatus;
  is_terminal: boolean;
  has_active_lease: boolean;
  has_dropped_lease: boolean;
  active_lease: LeasePublic | null;
  unresolved_blocker_count: number;
  is_blocked: boolean;
  unresolved_gate_count: number;
  is_gated: boolean;
  is_ready: boolean;
  display_state: WorkStatus | "active" | "dropped" | "blocked" | "waiting";
}

export interface WorkIdentityPointer {
  id: string;
  title: string;
  status: WorkStatus;
}

export interface WorkSummary {
  work_item: WorkItem;
  checkpoint_count: number;
  ancestor_path: WorkIdentityPointer[];
  ancestor_path_truncated: boolean;
  current_context: CheckpointPointer;
  readiness: Readiness;
}

export interface HierarchySummary {
  summary: WorkSummary;
  self_matches_filter: boolean;
  has_matching_descendants: boolean;
  presentation: HierarchyPresentation;
}

export interface HierarchyPresentation {
  direct_child_count: number;
  descendant_count: number;
  blocked_descendant_count: number;
  active_descendant_count: number;
  completed_descendant_count: number;
  discovered_descendant_count: number;
  branch_unresolved_human_gate_count: number;
  is_discovered_work: boolean;
  discovered_from_parent: boolean;
  next_active_descendant_lease_expires_at: string | null;
}

export type RelationshipType =
  | "blocks"
  | "parent-child"
  | "discovered-from"
  | "duplicate-of"
  | "related";

export type RelationshipDirection = "incoming" | "outgoing" | "undirected";

export interface ClientOperationInput {
  client_operation_id?: string;
}

export interface MutationActor {
  actor_client: string;
  actor_session_id: string;
  actor_model?: string | null;
}

export type WorkEventType =
  | "work_created"
  | "work_updated"
  | "work_status_changed"
  | "work_reopened"
  | "work_claimed"
  | "work_released"
  | "checkpoint_added"
  | "progress"
  | "dependency_added"
  | "dependency_removed"
  | "relationship_added"
  | "relationship_removed"
  | "human_attention_requested"
  | "human_attention_resolved"
  | "work_completed"
  | "work_deleted";

export type WorkEventOrigin = "live" | "backfill";
export type WorkEventActorKind = "client" | "unattributed";

export interface WorkSnapshot {
  title: string;
  summary: string;
  status: EventCreateWorkStatus;
  priority: number;
  version: 1;
}

export type WorkEventChangeSet = Partial<{
  title: { before: string; after: string };
  summary: { before: string; after: string };
  priority: { before: number; after: number };
  status: { before: EventWorkStatus; after: EventWorkStatus };
}>;

export type WorkEventMetadata =
  | Record<string, never>
  | { initial: WorkSnapshot }
  | { changes: WorkEventChangeSet; work_version: number }
  | {
      from_status: EventWorkStatus;
      to_status: EventWorkStatus;
      changes: WorkEventChangeSet;
      work_version: number;
    }
  | { expires_at: string }
  | { observed_expires_at: string; expiry_basis: "retained_lease_at_cutover" }
  | {
      lease_holder_kind: "client";
      lease_holder_client: string;
      lease_holder_session_id: string;
    }
  | { lease_holder_kind: "unattributed" }
  | { checkpoint_kind: "context" | "progress" }
  | { relationship_type: RelationshipType }
  | { gate_id: string; gate_type: "human" }
  | { from_status: "open" | "pending"; to_status: "done"; work_version: number }
  | { final_status: EventWorkStatus; final_version: number }
  | Record<string, unknown>;

export interface WorkEventRead {
  id: number;
  project_id: string;
  work_item_id: string;
  event_type: WorkEventType;
  actor_kind: WorkEventActorKind;
  actor_client: string | null;
  actor_session_id: string | null;
  actor_model: string | null;
  body: string | null;
  checkpoint_id: string | null;
  lease_generation_id: string | null;
  lease_release_id: string | null;
  relationship_id: string | null;
  relationship_source_work_item_id: string | null;
  relationship_target_work_item_id: string | null;
  relationship_context_checkpoint_work_item_id: string | null;
  relationship_context_checkpoint_id: string | null;
  relationship_direction: RelationshipDirection | null;
  counterpart_work_item_id: string | null;
  metadata_version: 1;
  metadata: WorkEventMetadata;
  origin: WorkEventOrigin;
  created_at: string;
}

export interface WorkEventPage extends Page<WorkEventRead> {
  pre_phase5_history_may_be_incomplete: boolean;
}

export interface ProgressEventInput extends ClientOperationInput {
  event_type: "progress";
  body: string;
  metadata: Record<string, unknown>;
  actor: MutationActor;
}

export interface RelationshipEdgeRead {
  id: string;
  project_id: string;
  relationship_type: RelationshipType;
  source_work_item_id: string;
  target_work_item_id: string;
  context_checkpoint_work_item_id: string | null;
  context_checkpoint_id: string | null;
  created_by_client: string;
  created_by_session_id: string;
  created_by_model: string | null;
  created_at: string;
}

export interface WorkPointer {
  id: string;
  title: string;
  status: WorkStatus;
  readiness: Readiness;
}

export interface AdjacentRelationshipRead {
  relationship: RelationshipEdgeRead;
  relative_to_work_item_id: string;
  direction: RelationshipDirection;
  counterpart: WorkPointer;
}

export interface RelationshipCreationResult {
  relationship: RelationshipEdgeRead;
  created: boolean;
}

export interface RelationshipRemovalResult {
  project_id: string;
  relationship_id: string;
  removed: boolean;
}

export interface RelationshipCreateInput extends ClientOperationInput {
  relationship_type: RelationshipType;
  source_work_item_id: string;
  target_work_item_id: string;
  created_by_client: string;
  created_by_session_id: string;
  created_by_model?: string | null;
  context_checkpoint_id?: string | null;
}

export interface InitialRelationshipInput {
  type: RelationshipType;
  direction: Exclude<RelationshipDirection, "undirected">;
  other_work_item_id: string;
  context_checkpoint_id?: string | null;
}

export interface RelationshipCounts {
  incoming: number;
  outgoing: number;
  undirected: number;
  total: number;
}

export interface HumanGateContextRevision {
  work_version: number;
  context_checkpoint_id: string;
  relationship_event_count: number;
}

export type HumanGateStatus = "unresolved" | "resolved";

export interface HumanGateRead {
  id: string;
  project_id: string;
  work_item_id: string;
  gate_type: "human";
  question: string;
  requested_by_client: string;
  requested_by_session_id: string;
  requested_by_model: string | null;
  requested_work_version: number;
  requested_context_checkpoint_id: string;
  requested_relationship_event_count: number;
  created_at: string;
  status: HumanGateStatus;
  current_context_revision: HumanGateContextRevision;
  work_changed_since_request: boolean;
  context_checkpoint_changed_since_request: boolean;
  relationships_changed_since_request: boolean;
  context_changed_since_request: boolean;
  resolved_at: string | null;
  resolution: string | null;
  resolved_by_client: string | null;
  resolved_by_session_id: string | null;
  resolved_by_model: string | null;
  resolved_context_revision: HumanGateContextRevision | null;
  context_changed_at_resolution: boolean | null;
  context_change_acknowledged: boolean | null;
}

export interface HumanAttentionItem {
  gate: HumanGateRead;
  summary: WorkSummary;
}

export interface CursorPage<T> {
  items: T[];
  total: number;
  limit: number;
  next_cursor: string | null;
}

export type HumanAttentionPage = CursorPage<HumanAttentionItem>;
export type HumanGatePage = CursorPage<HumanGateRead>;

export interface HumanGateResolutionInput extends ClientOperationInput {
  resolution: string;
  resolved_by_client: "dashboard";
  resolved_by_session_id: string;
  resolved_by_model?: null;
  acknowledge_context_change: boolean;
  reviewed_context_revision?: HumanGateContextRevision;
}

export interface WorkContext {
  work_item: WorkItem;
  initial_checkpoint: Checkpoint;
  // Null when the newest context checkpoint is the initial one; read
  // initial_checkpoint instead. Use currentContext() rather than reading this.
  current_context: Checkpoint | null;
  current_context_is_initial: boolean;
  recent_checkpoints: Checkpoint[];
  checkpoint_total: number;
  omitted_checkpoint_count: number;
  readiness: Readiness;
  incoming_relationships: AdjacentRelationshipRead[];
  outgoing_relationships: AdjacentRelationshipRead[];
  undirected_relationships: AdjacentRelationshipRead[];
  relationship_counts: RelationshipCounts;
  recent_events: WorkEventRead[];
  event_total: number;
  omitted_event_count: number;
  pre_phase5_history_may_be_incomplete: boolean;
  unresolved_gates: HumanGateRead[];
  unresolved_gate_total: number;
  omitted_unresolved_gate_count: number;
  recent_resolved_gates: HumanGateRead[];
  resolved_gate_total: number;
  omitted_resolved_gate_count: number;
}

export interface WorkCreateInput extends ClientOperationInput {
  title: string;
  summary: string;
  priority: number;
  status: MutableWorkStatus;
  initial_checkpoint: CheckpointInput;
  initial_relationships?: InitialRelationshipInput[];
}

export interface WorkCreation {
  work_item: WorkItem;
  initial_checkpoint: Checkpoint;
  initial_relationships: RelationshipEdgeRead[];
}

export interface CompletionResult {
  work_item: WorkItem;
  checkpoint: Checkpoint;
}

export interface DeletionResult {
  deleted: true;
  project_id: string;
  work_item_id: string;
  version: number;
}

export interface CheckpointInput {
  prompt: string;
  source_client: string;
  source_session_id: string;
  source_model?: string | null;
  source_session_url?: string | null;
  repository_branch?: string | null;
  verified_against?: string | null;
  tags?: string[];
  source_metadata?: Record<string, unknown>;
}

export interface CheckpointCreateInput extends CheckpointInput, ClientOperationInput {
  kind: Exclude<CheckpointKind, "completion">;
}

export interface WorkCompletionInput extends ClientOperationInput {
  expected_version: number;
  checkpoint: CheckpointInput;
}

export interface WorkDeletionInput extends ClientOperationInput {
  expected_version: number;
  actor: MutationActor;
}

export interface WorkDeferralInput extends ClientOperationInput {
  expected_version: number;
  actor: MutationActor;
}

export interface RelationshipRemovalInput extends ClientOperationInput {
  actor: MutationActor;
}

export interface WorkPatch extends ClientOperationInput {
  expected_version: number;
  title?: string;
  summary?: string;
  priority?: number;
  status?: MutableWorkStatus;
  actor?: MutationActor;
}
