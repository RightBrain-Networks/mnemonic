import { ApiError, detailMessage } from "./api.ts";
import { decodeDecision, type Decision, type DecisionContent, type DecisionState } from "./knowledge.ts";
import { finiteInteger, jsonEqual, objectValue, sameUuid, validUtcDateTime } from "./wire-guards.ts";

export const maximumDecisionBytes = 1024 * 1024;
export type DeletionTarget = { kind: "domain" | "decision"; id: string; name: string };
export type DeletionPreview = {
  project_id: string; domain_id: string; decision_id: string | null;
  document_count: number; relationship_count: number; removed_relationship_count: number; affected_review_count: number; relationships: Record<string, number>;
  expected_version: number | null; expected_revision: number | null; preview_token: string;
  blocked_reason: string | null;
};

export function decodeDeletionPreview(value: unknown, projectId: string, target: DeletionTarget): DeletionPreview {
  const item = objectValue(value);
  const relationships = objectValue(item?.relationships);
  if (!item || !sameUuid(item.project_id, projectId) || !relationships
    || !finiteInteger(item.document_count) || !finiteInteger(item.relationship_count)
    || !finiteInteger(item.removed_relationship_count) || !finiteInteger(item.affected_review_count)
    || item.relationship_count !== Number(item.removed_relationship_count) + Number(item.affected_review_count)
    || !Object.values(relationships).every((value) => finiteInteger(value))
    || typeof item.preview_token !== "string" || !/^[a-f0-9]{64}$/.test(item.preview_token)
    || !(item.blocked_reason === null || typeof item.blocked_reason === "string")
    || (target.kind === "domain" ? !sameUuid(item.domain_id, target.id) || item.decision_id !== null || !finiteInteger(item.expected_version, 1)
      : item.decision_id !== target.id || !finiteInteger(item.expected_revision))) {
    throw new Error("Mnemonic returned an invalid deletion preview. Reload before deleting.");
  }
  return item as DeletionPreview;
}

export function verifyDeletionResult(value: unknown, preview: DeletionPreview) {
  const item = objectValue(value);
  if (!item || !sameUuid(item.project_id, preview.project_id) || !sameUuid(item.domain_id, preview.domain_id)
    || item.decision_id !== preview.decision_id || item.preview_token !== preview.preview_token
    || item.document_count !== preview.document_count || item.relationship_count !== preview.relationship_count
    || item.removed_relationship_count !== preview.removed_relationship_count || item.affected_review_count !== preview.affected_review_count
    || !jsonEqual(item.relationships, preview.relationships) || !validUtcDateTime(item.deleted_at)) {
    throw new Error("The deletion outcome is unconfirmed. Retry the saved action.");
  }
}

export function deletionNeedsAcknowledgement(preview: DeletionPreview) {
  return preview.relationship_count > 0;
}

export async function readDecisionFile(file: File) {
  if (!/\.(?:md|markdown|txt)$/i.test(file.name)) throw new Error("Choose a Markdown (.md or .markdown) or plain text (.txt) file.");
  if (file.size > maximumDecisionBytes) throw new Error("Decision documents must be 1 MiB or smaller.");
  let content: string;
  try { content = new TextDecoder("utf-8", { fatal: true }).decode(await file.arrayBuffer()); }
  catch { throw new Error("Save this file as UTF-8 text before uploading it."); }
  if (content.includes("\0")) throw new Error("Decision documents cannot contain null characters.");
  return { content, media_type: /\.(?:md|markdown)$/i.test(file.name) ? "text/markdown" as const : "text/plain" as const,
    title: file.name.replace(/\.(?:md|markdown|txt)$/i, "").slice(0, 500) };
}

export function decisionWriteBody(draft: { domainId: string; title: string; content: string; mediaType: Decision["media_type"]; state: DecisionState }, basis?: DecisionContent) {
  if (!draft.domainId || !draft.title.trim()) throw new Error("Choose a domain and enter a document title.");
  if (new TextEncoder().encode(draft.content).byteLength > maximumDecisionBytes) throw new Error("Decision documents must be 1 MiB or smaller.");
  return {
    domain_id: basis?.domain_id ?? draft.domainId, title: draft.title.trim(), content: draft.content,
    media_type: draft.mediaType, state: draft.state, author_kind: "human", source_work_item_id: null,
    ...(basis ? { decision_id: basis.decision_id, expected_revision: basis.revision,
      related_decision_ids: basis.related_decision_ids, related_work_item_ids: basis.related_work_item_ids,
      related_artifact_ids: basis.related_artifact_ids, supersedes: basis.supersedes,
      depends_on: basis.depends_on ?? [], supersession_details: basis.supersession_details ?? [] } : {}),
    client_operation_id: crypto.randomUUID()
  };
}

export function decodeDecisionWrite(value: unknown, projectId: string, basis?: DecisionContent) {
  const result = objectValue(value);
  if (!result || typeof result.created !== "boolean") throw new Error("The save outcome is unconfirmed. Retry the saved action.");
  const decision = decodeDecision(result.decision, projectId, basis?.decision_id);
  if (basis && (decision.domain_id !== basis.domain_id || decision.revision < basis.revision || decision.revision > basis.revision + 1)) {
    throw new Error("The save returned an unexpected revision. Retry the saved action.");
  }
  return decision;
}

type SavePicker = (options: { suggestedName: string }) => Promise<{ createWritable: () => Promise<{ write: (data: Blob | Uint8Array) => Promise<void>; close: () => Promise<void>; abort: () => Promise<void> }> }>;
export async function downloadKnowledgeFile(path: string, filename: string) {
  const picker = (window as Window & { showSaveFilePicker?: SavePicker }).showSaveFilePicker;
  let handle: Awaited<ReturnType<SavePicker>> | undefined;
  if (picker) {
    try { handle = await picker.call(window, { suggestedName: filename }); }
    catch (error) { if (error instanceof DOMException && error.name === "AbortError") return; throw error; }
  }
  const response = await fetch(`/api/mnemonic${path}`, { cache: "no-store", credentials: "same-origin" });
  if (!response.ok) {
    const detail = detailMessage(objectValue(await response.json().catch(() => null))?.detail);
    throw new ApiError(detail.message || "Unable to download this file.", response.status, detail.code);
  }
  if (handle) {
    const writable = await handle.createWritable();
    const reader = response.body?.getReader();
    try {
      if (!reader) throw new Error("The download did not include a file body.");
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        await writable.write(value);
      }
      await writable.close();
    } catch (error) {
      await reader?.cancel().catch(() => undefined);
      await writable.abort().catch(() => undefined);
      throw error;
    } finally { reader?.releaseLock(); }
    return;
  }
  const url = URL.createObjectURL(await response.blob());
  const anchor = document.createElement("a");
  anchor.href = url; anchor.download = filename; document.body.append(anchor); anchor.click(); anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
}
