import type { CodeReviewContext, CodeReview, WorkFollowUp, ReviewPolicy, CodeReviewHandoff } from "./code-reviews.ts";
export type ExternalRecordState = "open" | "closed" | "merged" | "unknown";
export interface ExternalReference {
  url: string;
  kind: "tracked-by" | "references";
  label?: string;
  state: ExternalRecordState;
  state_observed_at?: string;
}
export interface ExternalCandidate {
  url: string;
  title: string;
  body: string;
  state: ExternalRecordState;
}
export interface ExternalSuggestionReference {
  url: string;
  title: string;
  state: ExternalRecordState;
}
export interface ExternalSuggestion {
  rank: number;
  signals: DuplicateSuggestionSignal[];
  reference: ExternalSuggestionReference;
}

export type WorkStatus = "pending" | "deferred" | "done" | "wont-do" | "promoted";
export type EventWorkStatus = "open" | WorkStatus;
export type EventCreateWorkStatus = Exclude<EventWorkStatus, "done">;
export type MutableWorkStatus = "pending" | "wont-do" | "promoted";
export type StatusFilter = WorkStatus | "active" | "dropped" | "all";
export type WorkSort = "updated" | "created" | "priority";
export type DuplicateScope = "canonical" | "aliases" | "all";
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
  job_completion_report_prompt: string;
  revision: string;
  code_review_required_min_priority: number;
  code_review_optional_min_priority: number;
  allow_remediation_code_reviews: boolean;
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface WorkItem {
  external_references?: ExternalReference[];
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
  affected_paths: string[];
}

export interface LeasePublic {
  purpose?: "code_review";
  code_review_id?: string;
  mode?: "cold" | "warm";
  holder_client: string;
  holder_session_id: string;
  acquired_at: string;
  renewed_at: string;
  expires_at: string;
}

export interface DashboardWorkActivationInput {
  expected_version: number;
  actor: MutationActor;
  claim_request_id: string;
}

export interface DashboardWorkPendingInput {
  expected_version: number;
  expected_lease_state: "active" | "dropped";
  expected_active_lease: LeasePublic | null;
  actor: MutationActor;
}

export interface LeaseReleaseResult {
  work_item_id: string;
  released: boolean;
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
  is_duplicate: boolean;
  canonical_work_item_id: string;
  is_ready: boolean;
  display_state: WorkStatus | "active" | "dropped" | "blocked" | "waiting" | "duplicate";
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

export interface WorkSearchHit {
  summary: WorkSummary;
  matched_member: WorkIdentityPointer;
}

export interface CanonicalWorkProjection {
  is_duplicate: boolean;
  direct_destination: WorkIdentityPointer | null;
  canonical_work_item: WorkIdentityPointer;
  path: WorkIdentityPointer[];
  duplicate_member_count: number;
}

export interface WorkItemDetailRead {
  code_review_context?: CodeReviewContext;
  work_item: WorkItem;
  canonical: CanonicalWorkProjection;
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
  branch_merged_duplicate_count: number;
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
  | "work_merged"
  | "work_moved"
  | "work_completed"
  | "work_follow_up_requested" | "work_follow_up_answered" | "work_follow_up_superseded"
  | "code_review_requested" | "code_review_completed" | "code_review_superseded"
  | "work_deleted";

export type WorkEventOrigin = "live" | "backfill";
export type WorkEventActorKind = "client" | "unattributed";

export interface WorkSnapshot {
  external_references?: ExternalReference[];
  title: string;
  summary: string;
  status: EventCreateWorkStatus;
  priority: number;
  version: 1;
}

export type WorkEventChangeSet = Partial<{
  external_references: { before: ExternalReference[]; after: ExternalReference[] };
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
  | {
      merge_id: string;
      source_work_item_id: string;
      destination_work_item_id: string;
      role: "source" | "destination";
      source_work_version: number;
      destination_work_version: number;
    }
  | {
      move_id: string;
      source_project_id: string;
      target_project_id: string;
      role: "source" | "target";
      work_version: number;
    }
  | { from_status: "open" | "pending"; to_status: "done"; work_version: number }
  | { final_status: EventWorkStatus; final_version: number }
  | Record<string, unknown>;

export interface WorkEventRead {
  code_review_id?: string;
  work_follow_up_id?: string;
  work_follow_up_answer_id?: string;
  code_review_result_id?: string;
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
  external_references?: ExternalReference[];
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
  requested_context_revision: HumanGateContextRevision;
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
  reviewed_context_revision: HumanGateContextRevision;
}

export interface WorkContext {
  code_review_context?: CodeReviewContext;
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
  merge_review_revision: MergeReviewRevision;
  canonical: CanonicalWorkProjection;
  duplicate_members: WorkIdentityPointer[];
  duplicate_member_total: number;
  omitted_duplicate_member_count: number;
  incoming_relationships: AdjacentRelationshipRead[];
  outgoing_relationships: AdjacentRelationshipRead[];
  undirected_relationships: AdjacentRelationshipRead[];
  relationship_counts: RelationshipCounts;
  omitted_relationship_counts: RelationshipCounts;
  duplicate_merge_eligibility: DuplicateMergeEligibility;
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

export interface MergeReviewRevision {
  work_version: number;
  context_checkpoint_id: string;
  work_event_count: number;
}

export type SourceLeaseState = "none" | "expired" | "active";

export interface DuplicateMergeEligibility {
  incident_blocks_count: number;
  incident_parent_child_count: number;
  has_unresolved_gate: boolean;
  source_lease_state: SourceLeaseState;
}

export interface WorkMergeInput extends ClientOperationInput {
  destination_work_item_id: string;
  reviewed_source_revision: MergeReviewRevision;
  reviewed_destination_revision: MergeReviewRevision;
  rationale: string;
  merged_by_client: "dashboard";
  merged_by_session_id: string;
  merged_by_model?: null;
}

export interface WorkMergeRead {
  id: string;
  merge_sequence: number;
  project_id: string;
  source_work_item_id: string;
  destination_work_item_id: string;
  duplicate_relationship_id: string;
  reviewed_source_revision: MergeReviewRevision;
  reviewed_destination_revision: MergeReviewRevision;
  resulting_source_work_version: number;
  resulting_destination_work_version: number;
  rationale: string;
  merged_by_client: string;
  merged_by_session_id: string;
  merged_by_model: string | null;
  created_at: string;
}

export interface WorkMergeResult {
  merge: WorkMergeRead;
  source_work_item: WorkItem;
  destination_work_item: WorkItem;
  direct_destination: WorkIdentityPointer;
  canonical_work_item: WorkIdentityPointer;
  supporting_relationship_created: boolean;
  supporting_relationship: RelationshipEdgeRead;
  relationship_events: WorkEventRead[];
  merge_events: WorkEventRead[];
}

export type DuplicateSuggestionMode = "hybrid_full" | "hybrid_shortlist" | "lexical";
export type DuplicateSuggestionSemanticScope =
  | "full_project"
  | "lexical_shortlist"
  | "unavailable";
export type DuplicateSuggestionSignal = "exact_title" | "lexical" | "semantic";

export interface DuplicateSuggestionInput {
  external_candidates?: ExternalCandidate[];
  title: string;
  summary: string;
  initial_prompt: string;
  tags: string[];
  exclude_work_item_id: string | null;
  limit: number;
}

export interface DuplicateCandidateSummary {
  external_references?: ExternalReference[];
  work_item_id: string;
  title: string;
  summary: string;
  status: WorkStatus;
  updated_at: string;
  duplicate_member_count: number;
}

export interface DuplicateSuggestion {
  canonical_work: DuplicateCandidateSummary;
  matched_member: WorkIdentityPointer;
  rank: number;
  signals: DuplicateSuggestionSignal[];
}

export interface DuplicateSuggestionPage {
  external_items?: ExternalSuggestion[];
  external_candidate_count?: number;
  external_scope?: "hybrid" | "lexical" | "unavailable";
  items: DuplicateSuggestion[];
  limit: number;
  mode: DuplicateSuggestionMode;
  semantic_available: boolean;
  semantic_scope: DuplicateSuggestionSemanticScope;
  composition_version: string;
  exact_title_group_total: number;
  omitted_exact_title_group_count: number;
}

export interface WorkCreateInput extends ClientOperationInput {
  external_references?: ExternalReference[];
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
  code_review_handoff?: CodeReviewHandoff;
  review_policy_decision?: ReviewPolicy;
  code_review_request?: CodeReview;
  agent_follow_ups?: WorkFollowUp[];
  work_item: WorkItem;
  checkpoint: Checkpoint;
  completion_evidence?: CompletionEvidencePayloadRead;
  job_completion_report?: JobCompletionReport;
}

export type VerificationType = "command" | "observation";
export type VerificationOutcome = "passed" | "failed" | "inconclusive" | "skipped";
export type ArtifactType =
  | "commit"
  | "pull_request"
  | "branch"
  | "test_run"
  | "repository_path"
  | "external_issue"
  | "build_artifact";

interface VerificationResultInputBase {
  verification_type: VerificationType;
  name: string;
  outcome: VerificationOutcome;
  summary: string;
  observed_at?: string;
  observed_at_commit?: string;
}

export interface CommandVerificationInput extends VerificationResultInputBase {
  verification_type: "command";
  command: string;
  exit_code?: number;
}

export interface ObservationVerificationInput extends VerificationResultInputBase {
  verification_type: "observation";
}

export type VerificationResultInput =
  | CommandVerificationInput
  | ObservationVerificationInput;

export interface ArtifactReferenceInput {
  artifact_type: ArtifactType;
  label: string;
  reference: string;
}

export interface CompletionEvidenceInput {
  verification_results?: VerificationResultInput[];
  artifact_references?: ArtifactReferenceInput[];
}

interface EvidenceChildRead {
  id: string;
  work_item_id: string;
  completion_checkpoint_id: string;
  position: number;
  created_at: string;
}

export type VerificationResultRead = VerificationResultInput & EvidenceChildRead;
export type ArtifactReferenceRead = ArtifactReferenceInput & EvidenceChildRead;

export interface CompletionEvidencePayloadRead {
  verification_results: VerificationResultRead[];
  artifact_references: ArtifactReferenceRead[];
}

export interface CompletionEvidenceEpisodeRead extends CompletionEvidencePayloadRead {
  completion_event_id: string;
  completion_checkpoint: CheckpointPointer;
}

export interface CompletionEvidencePage {
  work_item_id: string;
  work_version: number;
  lifecycle_status: WorkStatus;
  is_duplicate: boolean;
  canonical_work_item_id: string;
  current_completion_checkpoint_id: string | null;
  as_of_completion_event_id: string | null;
  items: CompletionEvidenceEpisodeRead[];
  total: number;
  structured_completion_total: number;
  limit: number;
  next_cursor: string | null;
}

export interface DeletionResult {
  deleted: true;
  project_id: string;
  work_item_id: string;
  version: number;
}

export interface WorkMoveResult {
  source_project_id: string;
  target_project_id: string;
  preserved_status: WorkStatus;
  work_item: WorkItem;
}

export interface CheckpointInput {
  prompt: string;
  source_client: string;
  source_session_id: string;
  source_model?: string | null;
  source_session_url?: string | null;
  repository_branch?: string | null;
  verified_against?: string | null;
  affected_paths?: string[];
  tags?: string[];
  source_metadata?: Record<string, unknown>;
}

export interface CheckpointCreateInput extends CheckpointInput, ClientOperationInput {
  kind: Exclude<CheckpointKind, "completion">;
}

export interface WorkCompletionInput extends ClientOperationInput {
  expected_version: number;
  checkpoint: CheckpointInput;
  completion_evidence?: CompletionEvidenceInput;
  job_completion_report?: JobCompletionReportInput;
}

export interface WorkDeletionInput extends ClientOperationInput {
  expected_version: number;
  actor: MutationActor;
}

export interface WorkMoveInput extends ClientOperationInput {
  target_project_id: string;
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

export interface WorkUpdate extends WorkItem {
  job_completion_report?: JobCompletionReport;
}

export interface WorkPatch extends ClientOperationInput {
  external_references?: ExternalReference[];
  job_completion_report?: JobCompletionReportInput;
  expected_version: number;
  title?: string;
  summary?: string;
  priority?: number;
  status?: MutableWorkStatus;
  actor?: MutationActor;
}


export type CloseoutStatus = "done" | "wont-do" | "promoted";
export interface JobCompletionReportInput {
  summary: string;
  fyi_items: string[];
  prompt_revision: string;
}
export interface JobCompletionReport extends JobCompletionReportInput {
  id: string;
  project_id: string;
  work_item_id: string;
  closeout_event_id: string;
  closeout_work_version: number;
  closeout_status: CloseoutStatus;
  completion_checkpoint_id: string | null;
  work_title_at_closeout: string;
  actor_client: string;
  actor_session_id: string;
  actor_model: string | null;
  prompt_sha256: string;
  created_at: string;
}
export interface JobReportSourceState {
  work_item_id: string;
  status: WorkStatus;
  canonical_work_item_id: string;
  deleted: boolean;
}
export interface JobReportDismissal {
  id: string;
  actor_client: string;
  actor_session_id: string;
  actor_model: string | null;
  created_at: string;
}
export interface JobReportEnvelope {
  created_sequence: string;
  report: JobCompletionReport;
  human_dismissed: boolean;
  human_dismissal: JobReportDismissal | null;
  source_work_state: JobReportSourceState;
  follow_up_count: string;
}
export interface JobReportDetail extends JobReportEnvelope {
  report: JobCompletionReport & { authoring_prompt: string };
}
export interface JobReportPage {
  project_id: string;
  stream_id: string;
  dismissal: "undismissed" | "dismissed" | "all";
  work_item_id: string | null;
  as_of_sequence: string;
  items: JobReportEnvelope[];
  has_more: boolean;
  next_cursor: string | null;
}
export interface JobReportCount {
  project_id: string;
  undismissed_count: string;
  as_of_sequence: string;
}
export interface JobReportDismissalResult {
  project_id: string;
  report_id: string;
  human_dismissal: JobReportDismissal;
  dismissed: boolean;
}
export interface JobReportFollowUp {
  id: string;
  project_id: string;
  report_id: string;
  created_sequence: string;
  source_work_item_id: string;
  follow_up_work_item_id: string;
  actor_client: string;
  actor_session_id: string;
  actor_model: string | null;
  created_at: string;
}
export interface JobReportFollowUpResult {
  work_item: WorkItem;
  initial_checkpoint: Checkpoint;
  follow_up: JobReportFollowUp;
}
export interface JobReportFollowUpInput extends ClientOperationInput {
  title: string;
  summary: string;
  priority: number;
  initial_checkpoint: CheckpointInput;
  actor: MutationActor;
}
export interface JobReportProvenancePage {
  project_id: string;
  report_id?: string;
  work_item_id?: string;
  direction?: "origin" | "created";
  items: JobReportFollowUp[];
  as_of_sequence: string;
  has_more: boolean;
  next_cursor: string | null;
}
export type ProjectActivityKind =
  | "work_event" | "job_completion_report_created" | "job_completion_report_dismissed"
  | "job_completion_report_follow_up_created" | "project_created" | "project_updated"
  | "project_settings_updated" | "lease_renewed";
export interface ProjectActivityItem {
  sequence: string;
  kind: ProjectActivityKind;
  work_event_id: string | null;
  event_type: WorkEventType | null;
  work_item_id: string | null;
  job_completion_report_id: string | null;
  human_dismissal_id: string | null;
  follow_up_id: string | null;
  settings_revision: string | null;
  lease_generation_id: string | null;
  recorded_at: string;
  origin: "live" | "history_import";
}
export interface ProjectActivityPage {
  project_id: string;
  stream_id: string;
  items: ProjectActivityItem[];
  next_cursor: string;
  has_more: boolean;
  through_sequence: string;
  historical_through_sequence: string;
  historical_coverage: "recorded_work_events_only";
}
