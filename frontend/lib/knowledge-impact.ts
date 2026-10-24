import { decodeSupersessionDelta, type SupersessionDelta } from "./supersession-deltas.ts";
import { decisionIdPattern, referencePattern, decisionStates, decodeKnowledgePage, validCommitHash, validScopeProvenance, type KnowledgePage, type DecisionScope, type DecisionState } from "./knowledge.ts";
import { boundedText, finiteInteger, nullableUuid, objectValue, sameUuid, validUuid } from "./wire-guards.ts";
import { validRevisionAuthor, type RevisionAuthorContext } from "./revision-authors.ts";

export const impactDispositions = ["still-valid", "change-required", "not-affected", "resolved"] as const;
export type ImpactDispositionName = typeof impactDispositions[number];
export type ImpactKind = "code-impact" | "context";
export type ImpactScope = Omit<DecisionScope, "project_id" | "decision_id" | "created_at">;
export type ImpactCandidate = {
  decision_id: string; reference: string; revision: number; title: string; state: DecisionState;
  is_current: boolean; relationship: "origin" | "depends-on" | "depends-on-prerequisite" | "related-only"; path: string[];
  related_direction?: "incoming" | "outgoing" | null;
  rationale: string | null; scopes: ImpactScope[]; related_work_item_ids: string[];
  related_artifact_ids: string[]; evidence_truncated: boolean;
  citation_excerpts?: { source_sha256: string; excerpts: string[]; truncated: boolean };
};
export type ImpactCoverage = {
  traversal_complete: boolean; provenance_complete: false; max_depth: number; snapshot_at?: string | null; notes?: string[];
  candidate_limit: number; visited_revisions: number; reasons: string[];
  warnings?: { code: "origin_has_no_linked_commits" | "supersession_extent_reason_unknown"; reference: string; message: string }[];
  related_semantics: "unknown"; path_policy: "one deterministic shortest path per revision";
};
export type DecisionImpact = KnowledgePage<ImpactCandidate> & { project_id: string; origin_reference: string; origin_author: RevisionAuthorContext; kind: ImpactKind; candidate_count: number; context_count: number; coverage: ImpactCoverage };
export type ImpactDiff = { changed_fields: string[]; before_sha256: string; after_sha256: string; unified_diff: string; truncated: boolean };
export type ImpactAssessment = {
  id: string; project_id: string; trigger: "revision" | "supersession"; before_reference: string;
  after_reference: string; extent: "full" | "partial" | null; reason: string | null;
  before_author: RevisionAuthorContext; after_author: RevisionAuthorContext; context_count: number;
  supersession_delta?: SupersessionDelta | null;
  diff: ImpactDiff; candidate_count: number; unassessed_count?: number; change_required_count?: number; status?: "pending" | "change-required" | "resolved"; coverage: ImpactCoverage; created_at: string; version: number;
};
export type ImpactDisposition = {
  id: string; project_id: string; assessment_id: string; version: number; candidate_reference: string;
  disposition: ImpactDispositionName; reason: string; remediation_work_item_id: string | null; created_at: string;
  actor: { actor_client: string; actor_session_id: string; actor_model?: string | null };
};
export type AssessmentDetail = ImpactAssessment & {
  kind: ImpactKind; total: number;
  candidates: ImpactCandidate[]; dispositions: ImpactDisposition[]; disposition_history_total: number;
  limit: number; offset: number; next_offset: number | null;
};
function invalid(): never { throw new Error("Mnemonic returned invalid decision impact data. Refresh to try again."); }
function strings(value: unknown): value is string[] { return Array.isArray(value) && value.every((item) => typeof item === "string"); }
function reference(value: unknown): value is string { return typeof value === "string" && referencePattern.test(value); }
function requiresDisposition(candidate: ImpactCandidate): boolean {
  return ["origin", "depends-on"].includes(candidate.relationship)
    || candidate.relationship === "related-only" && candidate.related_direction === "incoming";
}
function validKindPage(item: Record<string, unknown>, candidates: ImpactCandidate[]) {
  return ["code-impact", "context"].includes(String(item.kind)) && finiteInteger(item.candidate_count)
    && finiteInteger(item.context_count) && item.total === (item.kind === "code-impact" ? item.candidate_count : item.context_count)
    && candidates.every((candidate) => requiresDisposition(candidate) === (item.kind === "code-impact"));
}
function decodeCoverage(value: unknown): ImpactCoverage {
  const item = objectValue(value);
  if (!item || typeof item.traversal_complete !== "boolean" || item.provenance_complete !== false
    || !finiteInteger(item.max_depth, 1) || !finiteInteger(item.candidate_limit, 1) || !finiteInteger(item.visited_revisions)
    || !(item.snapshot_at == null || typeof item.snapshot_at === "string") || !(item.notes === undefined || strings(item.notes))
    || !strings(item.reasons) || item.related_semantics !== "unknown"
    || item.path_policy !== "one deterministic shortest path per revision") invalid();
  if (item.warnings !== undefined && (!Array.isArray(item.warnings) || !item.warnings.every((value) => {
    const warning = objectValue(value);
    return warning && (warning.code === "origin_has_no_linked_commits" || warning.code === "supersession_extent_reason_unknown") && reference(warning.reference) && typeof warning.message === "string";
  }))) invalid();
  return item as ImpactCoverage;
}
export function decodeImpactCandidate(value: unknown): ImpactCandidate {
  const item = objectValue(value);
  if (!item || typeof item.decision_id !== "string" || !decisionIdPattern.test(item.decision_id)
    || !finiteInteger(item.revision) || item.reference !== `${item.decision_id}-${item.revision}`
    || typeof item.title !== "string" || !decisionStates.includes(item.state as DecisionState)
    || typeof item.is_current !== "boolean" || !["origin", "depends-on", "depends-on-prerequisite", "related-only"].includes(String(item.relationship))
    || !(item.related_direction == null || item.relationship === "related-only" && (item.related_direction === "incoming" || item.related_direction === "outgoing"))
    || !strings(item.path) || !item.path.length || !item.path.every(reference) || item.path.at(-1) !== item.reference
    || !(item.rationale === null || boundedText(item.rationale, 4000))
    || !Array.isArray(item.scopes) || !item.scopes.every((value) => {
      const scope = objectValue(value);
      return scope && validUuid(scope.id) && validCommitHash(scope.commit_hash) && scope.revision === item.revision
        && nullableUuid(scope.work_item_id) && (scope.agent_session_id === null || typeof scope.agent_session_id === "string") && validScopeProvenance(scope, 10);
    }) || !strings(item.related_work_item_ids) || !item.related_work_item_ids.every(validUuid)
    || !strings(item.related_artifact_ids) || !item.related_artifact_ids.every(validUuid) || typeof item.evidence_truncated !== "boolean") invalid();
  if (item.citation_excerpts !== undefined) {
    const evidence = objectValue(item.citation_excerpts);
    if (!evidence || typeof evidence.source_sha256 !== "string" || !/^[a-f0-9]{64}$/.test(evidence.source_sha256)
      || !strings(evidence.excerpts) || evidence.excerpts.length > 3 || !evidence.excerpts.every(text => text.length > 0 && Array.from(text).length <= 400)
      || typeof evidence.truncated !== "boolean") invalid();
  }
  return item as ImpactCandidate;
}
export function decodeImpact(value: unknown, projectId: string, originReference?: string, kind: ImpactKind = "code-impact"): DecisionImpact {
  const item = objectValue(value);
  if (!item || !sameUuid(item.project_id, projectId) || !reference(item.origin_reference)
    || originReference !== undefined && item.origin_reference !== originReference) invalid();
  const page = decodeKnowledgePage(item, decodeImpactCandidate);
  if (page.items.some((candidate) => candidate.path[0] !== item.origin_reference)
    || !validKindPage(item, page.items) || item.kind !== kind || !validRevisionAuthor(item.origin_author, item.origin_reference as string)) invalid();
  const coverage = decodeCoverage(item.coverage);
  if (coverage.warnings?.some((warning) => warning.reference !== item.origin_reference)) invalid();
  return { ...item, ...page, coverage } as DecisionImpact;
}
export function decodeAssessment(value: unknown, projectId: string): ImpactAssessment {
  const item = objectValue(value), diff = objectValue(item?.diff);
  if (!item || !validUuid(item.id) || !sameUuid(item.project_id, projectId)
    || !["revision", "supersession"].includes(String(item.trigger)) || !reference(item.before_reference) || !reference(item.after_reference)
    || !(item.extent === null || item.extent === "full" || item.extent === "partial")
    || !(item.reason === null || boundedText(item.reason, 4000)) || !finiteInteger(item.candidate_count)
    || typeof item.created_at !== "string" || !finiteInteger(item.version)
    || !finiteInteger(item.context_count) || !validRevisionAuthor(item.before_author, item.before_reference as string)
    || !validRevisionAuthor(item.after_author, item.after_reference as string)
    || !diff || !strings(diff.changed_fields) || typeof diff.unified_diff !== "string" || typeof diff.truncated !== "boolean"
    || typeof diff.before_sha256 !== "string" || !/^[a-f0-9]{64}$/.test(diff.before_sha256)
    || typeof diff.after_sha256 !== "string" || !/^[a-f0-9]{64}$/.test(diff.after_sha256)) invalid();
  if (item.status !== undefined || item.unassessed_count !== undefined || item.change_required_count !== undefined) {
    if (!finiteInteger(item.unassessed_count, 0, item.candidate_count as number)
      || !finiteInteger(item.change_required_count, 0, (item.candidate_count as number) + (item.context_count as number) - item.unassessed_count)
      || item.status !== (item.change_required_count > 0 ? "change-required" : item.unassessed_count > 0 ? "pending" : "resolved")) invalid();
  }
  const coverage = decodeCoverage(item.coverage);
  if (coverage.warnings?.some((warning) => warning.reference !== item.before_reference)) invalid();
  const delta = item.supersession_delta == null ? item.supersession_delta : decodeSupersessionDelta(
    item.supersession_delta, item.before_reference as string, item.after_reference as string, String(item.trigger));
  return { ...item, coverage, ...(delta === undefined ? {} : { supersession_delta: delta }) } as ImpactAssessment;
}
export function decodeDisposition(value: unknown, projectId: string, assessmentId: string): ImpactDisposition {
  const item = objectValue(value);
  if (!item || !validUuid(item.id) || !sameUuid(item.project_id, projectId) || !sameUuid(item.assessment_id, assessmentId)
    || !finiteInteger(item.version, 1) || !reference(item.candidate_reference)
    || !impactDispositions.includes(item.disposition as ImpactDispositionName) || !boundedText(item.reason, 4000)
    || !nullableUuid(item.remediation_work_item_id) || typeof item.created_at !== "string"
    || !boundedText(objectValue(item.actor)?.actor_client, 80) || !boundedText(objectValue(item.actor)?.actor_session_id, 200)
    || !(objectValue(item.actor)?.actor_model == null || boundedText(objectValue(item.actor)?.actor_model, 120))) invalid();
  return item as ImpactDisposition;
}
export function decodeAssessmentDetail(value: unknown, projectId: string, assessmentId: string, kind: ImpactKind = "code-impact"): AssessmentDetail {
  const item = objectValue(value), assessment = decodeAssessment(value, projectId);
  if (!item || !sameUuid(assessment.id, assessmentId) || !Array.isArray(item.dispositions) || !finiteInteger(item.disposition_history_total)) invalid();
  const page = decodeKnowledgePage({ items: item.candidates, total: item.total, limit: item.limit, offset: item.offset, next_offset: item.next_offset }, decodeImpactCandidate);
  if (!validKindPage(item, page.items) || item.kind !== kind) invalid();
  const dispositions = item.dispositions.map((entry) => decodeDisposition(entry, projectId, assessmentId));
  if (dispositions.some((entry) => entry.version > assessment.version || !page.items.some((candidate) => candidate.reference === entry.candidate_reference))
    || dispositions.length > page.items.length || new Set(dispositions.map((entry) => entry.candidate_reference)).size !== dispositions.length) invalid();
  return { ...assessment, kind, total: page.total, candidates: page.items, dispositions, disposition_history_total: item.disposition_history_total,
    limit: page.limit, offset: page.offset, next_offset: page.next_offset ?? null };
}
