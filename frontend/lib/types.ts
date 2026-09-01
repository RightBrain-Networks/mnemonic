export type WorkStatus = "open" | "done" | "wont-do" | "promoted";
export type MutableWorkStatus = Exclude<WorkStatus, "done">;
export type StatusFilter = WorkStatus | "all";
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
  active_lease: LeasePublic | null;
  unresolved_blocker_count: number;
  is_blocked: boolean;
  is_ready: boolean;
  display_state: WorkStatus | "ready" | "active" | "blocked";
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
}

export type RelationshipType =
  | "blocks"
  | "parent-child"
  | "discovered-from"
  | "duplicate-of"
  | "related";

export type RelationshipDirection = "incoming" | "outgoing" | "undirected";

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

export interface RelationshipCreateInput {
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

export interface WorkPatch {
  expected_version: number;
  title?: string;
  summary?: string;
  priority?: number;
  status?: MutableWorkStatus;
}
