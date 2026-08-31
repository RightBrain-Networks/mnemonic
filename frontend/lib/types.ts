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

export interface Readiness {
  lifecycle_status: WorkStatus;
  is_terminal: boolean;
  has_active_lease: boolean;
  active_lease: null;
  unresolved_blocker_count: number;
  is_blocked: boolean;
  is_ready: boolean;
  display_state: WorkStatus | "ready" | "active" | "blocked";
}

export interface WorkSummary {
  work_item: WorkItem;
  checkpoint_count: number;
  ancestor_path: Array<Pick<WorkItem, "id" | "title" | "status">>;
  ancestor_path_truncated: boolean;
  current_context: CheckpointPointer;
  readiness: Readiness;
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
  current_context: Checkpoint;
  recent_checkpoints: Checkpoint[];
  checkpoint_total: number;
  omitted_checkpoint_count: number;
  readiness: Readiness;
  incoming_relationships: [];
  outgoing_relationships: [];
  undirected_relationships: [];
  relationship_counts: RelationshipCounts;
}

export interface WorkCreation {
  work_item: WorkItem;
  initial_checkpoint: Checkpoint;
  initial_relationships: [];
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

// Compatibility aliases remain during the Phase 1 cutover.
export type HandoffStatus = WorkStatus;
export type HandoffCommentKind = "comment" | "work-summary";
