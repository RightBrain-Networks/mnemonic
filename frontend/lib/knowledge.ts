import { decodeDecisionRevisionChange, type DecisionRevisionChange } from "./decision-changes.ts";
import { validDomainDecisionReviewSetting, type DomainDecisionReviewSetting } from "./automatic-decision-reviews.ts";
import { ApiError, detailMessage } from "./api.ts";
import { readBoundedJson } from "./bounded-json.ts";
import { exactKeys, finiteInteger, objectValue, sameUuid, validUuid } from "./wire-guards.ts";

export const decisionStates = ["proposed", "rejected", "accepted", "superseded", "partially-superseded"] as const;
export type DecisionState = typeof decisionStates[number];
export type KnowledgeDomain = {
  id: string; project_id: string; name: string; prefix: string; version: number;
  created_at: string; updated_at: string; decision_count: number;
  automatic_decision_reviews: DomainDecisionReviewSetting;
  is_product_decision_domain?: boolean;
};
export type Decision = {
  change?: DecisionRevisionChange | null;
  id: string; project_id: string; domain_id: string; domain_name: string; prefix: string;
  ordinal: number; decision_id: string; reference: string; revision: number; title: string;
  state: DecisionState; media_type: "text/plain" | "text/markdown"; size_bytes: number;
  sha256: string; created_at: string; updated_at: string; author_kind: "human" | "agent";
  agent_session_id: string | null; model_id: string | null; related_decision_ids: string[];
  related_work_item_ids: string[]; related_artifact_ids: string[]; supersedes: string[];
  superseded_by: string[]; referenced_by: string[]; scope_count: number;
  depends_on?: DecisionDependency[]; supersession_details?: SupersessionDetail[];
  human_gate?: { gate_id: string; work_item_id: string; work_project_id: string; status: "unresolved" | "resolved"; question_version: number };
};
export type DecisionDependency = { decision_id: string; revision: number; rationale: string };
export type SupersessionDetail = { decision_id: string; revision: number; extent: "full" | "partial"; reason: string };
export type DecisionContent = Decision & { content: string; total_lines: number; line_start: number; line_end: number };
export type ScopeCompletionCitation = {
  report_id: string; work_item_id: string; actor_client: string;
  actor_session_id: string | null; actor_model: string | null; created_at: string;
};
export type DecisionScope = {
  id: string; project_id: string; decision_id: string; revision: number; commit_hash: string;
  agent_session_id: string | null; work_item_id: string | null; created_at: string;
  repository?: string | null; completion_report_id?: string | null; merged_commit_hashes?: string[];
  completion_citations?: ScopeCompletionCitation[]; completion_citations_truncated?: boolean;
};
export type KnowledgePage<T> = { items: T[]; total: number; limit: number; offset: number; next_offset?: number | null };

export function knowledgePath(projectId: string, suffix = "") {
  return `/projects/${encodeURIComponent(projectId)}/knowledge${suffix}`;
}

export function knowledgeLibraryPath(projectId: string, decisionId?: string, revision?: number) {
  const params = new URLSearchParams({ project: projectId });
  if (decisionId) params.set("decision", decisionId);
  if (revision !== undefined) params.set("revision", String(revision));
  return `/knowledge?${params}`;
}

export function normalizeDomainPrefix(value: string) {
  const prefix = value.trim().toUpperCase();
  if (!/^[A-Z]{1,5}$/.test(prefix)) throw new Error("Use one to five letters (A–Z) for the prefix.");
  return prefix;
}

function invalid(): never { throw new Error("Mnemonic returned invalid knowledge graph data. Refresh to try again."); }
function strings(value: unknown): value is string[] { return Array.isArray(value) && value.every((item) => typeof item === "string"); }
export const decisionIdPattern = /^[A-Z]{1,5}-\d{5,}$/;
export const referencePattern = /^[A-Z]{1,5}-\d{5,}-\d+$/;

export function decodeDomain(value: unknown, projectId: string): KnowledgeDomain {
  const item = objectValue(value);
  if (!item || !validUuid(item.id) || !sameUuid(item.project_id, projectId)
    || typeof item.name !== "string" || !item.name.trim() || typeof item.prefix !== "string"
    || !/^[A-Z]{1,5}$/.test(item.prefix) || !finiteInteger(item.version, 1)
    || !finiteInteger(item.decision_count) || typeof item.created_at !== "string"
    || typeof item.updated_at !== "string"
    || !validDomainDecisionReviewSetting(item.automatic_decision_reviews)
    || item.is_product_decision_domain !== undefined && typeof item.is_product_decision_domain !== "boolean"
    || item.is_product_decision_domain === true && item.prefix !== "PDR") invalid();
  return item as KnowledgeDomain;
}

export function decodeDecision(value: unknown, projectId: string, decisionId?: string): Decision {
  const item = objectValue(value);
  if (!item || !validUuid(item.id) || !sameUuid(item.project_id, projectId) || !validUuid(item.domain_id)
    || typeof item.decision_id !== "string" || !decisionIdPattern.test(item.decision_id)
    || (decisionId !== undefined && item.decision_id !== decisionId)
    || typeof item.prefix !== "string" || !/^[A-Z]{1,5}$/.test(item.prefix)
    || !finiteInteger(item.ordinal, 1) || !finiteInteger(item.revision)
    || item.decision_id !== `${item.prefix}-${String(item.ordinal).padStart(5, "0")}`
    || item.reference !== `${item.decision_id}-${item.revision}`
    || typeof item.domain_name !== "string" || typeof item.title !== "string"
    || !decisionStates.includes(item.state as DecisionState)
    || !["text/plain", "text/markdown"].includes(String(item.media_type))
    || !finiteInteger(item.size_bytes) || typeof item.sha256 !== "string" || !/^[a-f0-9]{64}$/.test(item.sha256)
    || typeof item.created_at !== "string" || typeof item.updated_at !== "string"
    || !["human", "agent"].includes(String(item.author_kind))
    || !(item.agent_session_id === null || typeof item.agent_session_id === "string")
    || !(item.model_id === null || typeof item.model_id === "string") || !finiteInteger(item.scope_count)
    || !strings(item.related_decision_ids) || !item.related_decision_ids.every((id) => decisionIdPattern.test(id))
    || !strings(item.related_work_item_ids) || !item.related_work_item_ids.every(validUuid)
    || !strings(item.related_artifact_ids) || !item.related_artifact_ids.every(validUuid)
    || !strings(item.supersedes) || !item.supersedes.every((id) => decisionIdPattern.test(id))
    || !strings(item.superseded_by) || !item.superseded_by.every((id) => referencePattern.test(id))
    || !strings(item.referenced_by) || !item.referenced_by.every((id) => referencePattern.test(id))) invalid();
  if (item.depends_on !== undefined && (!Array.isArray(item.depends_on) || !item.depends_on.every((value) => validDecisionEdge(value, "rationale")))
    || item.supersession_details !== undefined && (!Array.isArray(item.supersession_details) || !item.supersession_details.every((value) => validDecisionEdge(value, "reason") && ["full", "partial"].includes(String(objectValue(value)?.extent))))) invalid();
  if (item.human_gate !== undefined) {
    const gate = objectValue(item.human_gate);
    if (!gate || !exactKeys(gate, ["gate_id", "work_item_id", "work_project_id", "status", "question_version"])
      || !validUuid(gate.gate_id) || !validUuid(gate.work_item_id) || !validUuid(gate.work_project_id)
      || !["unresolved", "resolved"].includes(String(gate.status)) || !finiteInteger(gate.question_version, 1)
      || item.prefix !== "PDR"
      || (!sameUuid(gate.work_project_id, projectId) ? gate.status !== "resolved" : !item.related_work_item_ids.includes(gate.work_item_id))) invalid();
  }
  if (item.change != null) decodeDecisionRevisionChange(item.change);
  return item as Decision;
}

export function decodeDecisionContent(value: unknown, projectId: string, decisionId: string, revision?: number): DecisionContent {
  const decision = decodeDecision(value, projectId, decisionId);
  const item = value as Record<string, unknown>;
  if (revision !== undefined && decision.revision !== revision || typeof item.content !== "string"
    || !finiteInteger(item.total_lines) || !finiteInteger(item.line_start)
    || !finiteInteger(item.line_end) || item.line_end > item.total_lines) invalid();
  return item as DecisionContent;
}

export async function verifyDecisionContent(decision: DecisionContent): Promise<DecisionContent> {
  const bytes = new TextEncoder().encode(decision.content);
  const hash = await crypto.subtle.digest("SHA-256", bytes);
  const sha256 = Array.from(new Uint8Array(hash), (byte) => byte.toString(16).padStart(2, "0")).join("");
  if (bytes.byteLength !== decision.size_bytes || sha256 !== decision.sha256) {
    throw new Error("The document does not match its retained revision. Close it and try again.");
  }
  return decision;
}

export function decodeScope(value: unknown, projectId: string, decisionId?: string): DecisionScope {
  const item = objectValue(value);
  if (!item || !validUuid(item.id) || !sameUuid(item.project_id, projectId)
    || typeof item.decision_id !== "string" || !decisionIdPattern.test(item.decision_id)
    || decisionId !== undefined && item.decision_id !== decisionId || !finiteInteger(item.revision)
    || typeof item.commit_hash !== "string" || !/^(?:[a-fA-F0-9]{40}|[a-fA-F0-9]{64})$/.test(item.commit_hash)
    || !(item.agent_session_id === null || typeof item.agent_session_id === "string")
    || !(item.work_item_id === null || validUuid(item.work_item_id)) || typeof item.created_at !== "string") invalid();
  if (!validScopeProvenance(item)) invalid();
  return item as DecisionScope;
}

export function decodeKnowledgePage<T>(value: unknown, decode: (value: unknown) => T): KnowledgePage<T> {
  const page = objectValue(value);
  if (!page || !Array.isArray(page.items) || !finiteInteger(page.total)
    || !finiteInteger(page.offset) || !finiteInteger(page.limit, 1)
    || page.items.length > page.limit || page.items.length > 0 && page.items.length + page.offset > page.total
    || !(page.next_offset === undefined || page.next_offset === null || finiteInteger(page.next_offset, page.offset + 1))) invalid();
  return { ...page, items: page.items.map(decode) } as KnowledgePage<T>;
}

export function knowledgeNextOffset<T>(page: KnowledgePage<T>) {
  return page.next_offset !== undefined ? page.next_offset
    : page.offset + page.items.length < page.total && page.items.length > 0 ? page.offset + page.items.length : null;
}

export function selectedDecision(search: string): { id: string; revision?: number } | null {
  const params = new URLSearchParams(search);
  const id = params.get("decision");
  if (!id) return null;
  if (referencePattern.test(id)) {
    const parts = id.split("-");
    return { id: `${parts[0]}-${parts[1]}`, revision: Number(parts[2]) };
  }
  if (!decisionIdPattern.test(id)) return null;
  const revision = params.get("revision");
  return revision !== null && /^\d+$/.test(revision) && Number.isSafeInteger(Number(revision))
    ? { id, revision: Number(revision) } : { id };
}

export async function knowledgeJson(path: string, init: RequestInit = {}): Promise<unknown> {
  const response = await fetch(`/api/mnemonic${path}`, {
    ...init, cache: "no-store", credentials: "same-origin",
    headers: { Accept: "application/json", ...(init.body ? { "Content-Type": "application/json" } : {}), ...init.headers }
  });
  const value = await readBoundedJson(response, 16 * 1024 * 1024);
  if (!response.ok) {
    const detail = detailMessage(objectValue(value)?.detail);
    throw new ApiError(detail.message || "Unable to load the knowledge graph.", response.status, detail.code);
  }
  return value;
}

export function validDecisionEdge(value: unknown, reason: "reason" | "rationale") {
  const edge = objectValue(value);
  return Boolean(edge && typeof edge.decision_id === "string" && decisionIdPattern.test(edge.decision_id)
    && finiteInteger(edge.revision, 0, 2_147_483_647) && typeof edge[reason] === "string"
    && (edge[reason] as string).trim() && !(edge[reason] as string).includes("\0"));
}
export function validCommitHash(value: unknown): value is string {
  return typeof value === "string" && /^(?:[a-fA-F0-9]{40}|[a-fA-F0-9]{64})$/.test(value);
}
function validCompletionCitation(value: unknown) {
  const citation = objectValue(value);
  return Boolean(citation && validUuid(citation.report_id) && validUuid(citation.work_item_id)
    && typeof citation.actor_client === "string" && citation.actor_client.trim()
    && (citation.actor_session_id === null || typeof citation.actor_session_id === "string" && citation.actor_session_id.trim())
    && (citation.actor_model === null || typeof citation.actor_model === "string" && citation.actor_model.trim())
    && typeof citation.created_at === "string" && Number.isFinite(Date.parse(citation.created_at)));
}
export function validScopeProvenance(item: Record<string, unknown>, citationLimit = 50) {
  return (item.repository === undefined || item.repository === null || typeof item.repository === "string" && Boolean(item.repository.trim()))
    && (item.completion_report_id === undefined || item.completion_report_id === null || validUuid(item.completion_report_id))
    && (item.merged_commit_hashes === undefined || Array.isArray(item.merged_commit_hashes) && item.merged_commit_hashes.every(validCommitHash))
    && (item.completion_citations === undefined || Array.isArray(item.completion_citations)
      && item.completion_citations.length <= citationLimit && item.completion_citations.every(validCompletionCitation)
      && new Set(item.completion_citations.map((citation) => citation.report_id.toLowerCase())).size === item.completion_citations.length)
    && (item.completion_citations_truncated === undefined || typeof item.completion_citations_truncated === "boolean");
}
