import { decisionIdPattern, validCommitHash } from "./knowledge.ts";
import { boundedText, exactKeys, finiteInteger, objectValue } from "./wire-guards.ts";

export type DecisionProvenance = {
  disposition: "cited" | "no-applicable-decisions" | "not-code";
  reason: string | null;
  citations: { decision_id: string; revision: number }[];
  commits: { repository: string; commit_hash: string }[];
};
export type ProvenanceDraft = {
  disposition: DecisionProvenance["disposition"] | ""; reason: string;
  citations: { decision_id: string; revision: string }[];
  commits: { repository: string; commit_hash: string }[];
};
export const emptyProvenanceDraft = (): ProvenanceDraft => ({ disposition: "", reason: "", citations: [], commits: [] });
export function validRepository(value: unknown): value is string {
  return boundedText(value, 500) && !/[\s\x00-\x1f?#]/u.test(value) && !(value.includes("://") && value.split("://", 2)[1]?.includes("@"));
}
export function validDecisionProvenance(value: unknown): value is DecisionProvenance {
  const item = objectValue(value);
  if (!item || !exactKeys(item, ["disposition", "reason", "citations", "commits"])
    || !["cited", "no-applicable-decisions", "not-code"].includes(String(item.disposition))
    || !(item.reason === null || boundedText(item.reason, 2000))
    || !Array.isArray(item.citations) || item.citations.length > 100
    || !Array.isArray(item.commits) || item.commits.length > 100) return false;
  const citations = item.citations.map(objectValue), commits = item.commits.map(objectValue);
  if (!citations.every((citation) => citation && exactKeys(citation, ["decision_id", "revision"])
    && typeof citation.decision_id === "string" && decisionIdPattern.test(citation.decision_id) && finiteInteger(citation.revision, 0, 2_147_483_647))
    || !commits.every((commit) => commit && exactKeys(commit, ["repository", "commit_hash"]) && validRepository(commit.repository) && validCommitHash(commit.commit_hash))
    || new Set(citations.map((citation) => `${citation!.decision_id}-${citation!.revision}`)).size !== citations.length
    || new Set(commits.map((commit) => `${commit!.repository}\0${String(commit!.commit_hash).toLowerCase()}`)).size !== commits.length
    || citations.length * commits.length > 500) return false;
  return item.disposition === "cited" ? citations.length > 0 && commits.length > 0
    : Boolean(item.reason) && citations.length === 0 && (item.disposition !== "not-code" || commits.length === 0);
}
export function provenanceFromDraft(draft: ProvenanceDraft): DecisionProvenance {
  const value = { disposition: draft.disposition, reason: draft.reason.trim() || null,
    citations: draft.citations.map((entry) => ({ decision_id: entry.decision_id.trim().toUpperCase(), revision: /^\d+$/.test(entry.revision) ? Number(entry.revision) : -1 })),
    commits: draft.commits.map((entry) => ({ repository: entry.repository.trim(), commit_hash: entry.commit_hash.trim().toLowerCase() })) };
  if (!validDecisionProvenance(value)) throw new Error("Declare decision use: cited decisions need exact revisions and full commit hashes with repository identities. Other conclusions need a reason. Remove duplicate entries and keep at most 500 citation/commit pairs.");
  return value;
}
