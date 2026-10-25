"use client";

import { useEffect, useId, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ApiError, errorMessage } from "@/lib/api";
import { knowledgeJson, knowledgePath } from "@/lib/knowledge";
import { decodeDeletionPreview, deletionNeedsAcknowledgement, verifyDeletionResult, type DeletionPreview, type DeletionTarget } from "@/lib/knowledge-management";

type Intent = { path: string; body: string; preview: DeletionPreview };
export default function KnowledgeDeleteDialog({ projectId, target, onClose, onDeleted, onPendingChange }: {
  projectId: string; target: DeletionTarget; onClose: () => void; onDeleted: () => void; onPendingChange: (pending: boolean) => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const [acknowledged, setAcknowledged] = useState(false);
  const [pending, setPending] = useState<Intent | null>(null);
  const [busy, setBusy] = useState(false);
  const dispatching = useRef(false);
  const [error, setError] = useState("");
  const path = knowledgePath(projectId, `/${target.kind === "domain" ? "domains" : "decisions"}/${encodeURIComponent(target.id)}`);
  const preview = useQuery({ queryKey: ["knowledge-deletion", projectId, target.kind, target.id],
    queryFn: async ({ signal }) => decodeDeletionPreview(await knowledgeJson(`${path}/deletion-preview`, { signal }), projectId, target),
    retry: false, staleTime: 0, gcTime: 0, refetchOnWindowFocus: false });
  const details = preview.isError ? undefined : preview.data;
  const needsAcknowledgement = details && deletionNeedsAcknowledgement(details);
  useEffect(() => {
    const element = dialog.current!;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    element.showModal();
    return () => { element.close(); previous?.focus({ preventScroll: true }); };
  }, []);
  useEffect(() => { setAcknowledged(false); }, [details?.preview_token]);

  async function execute(intent: Intent) {
    if (dispatching.current) return;
    dispatching.current = true; setBusy(true); setPending(intent); onPendingChange(true); setError("");
    try {
      const result = await knowledgeJson(intent.path, { method: "POST", body: intent.body });
      verifyDeletionResult(result, intent.preview);
      setPending(null); onPendingChange(false); onDeleted();
    } catch (failure) {
      if (failure instanceof ApiError && failure.status >= 400 && failure.status < 500 && failure.status !== 408) {
        setPending(null); onPendingChange(false);
        if (failure.status === 409) { setAcknowledged(false); void preview.refetch(); }
      }
      setError(errorMessage(failure));
    } finally { dispatching.current = false; setBusy(false); }
  }
  function remove() {
    if (!details || preview.isFetching || details.blocked_reason || pending || needsAcknowledgement && !acknowledged) return;
    void execute({ path: `${path}/delete`, preview: details, body: JSON.stringify({
      ...(target.kind === "domain" ? { expected_version: details.expected_version } : { expected_revision: details.expected_revision }),
      expected_preview: details.preview_token, acknowledge_relationships: acknowledged, client_operation_id: crypto.randomUUID()
    }) });
  }

  return <dialog ref={dialog} className="dialog" aria-labelledby={titleId} onCancel={(event) => { event.preventDefault(); if (!pending) onClose(); }}>
    <div className="dialog-header"><h2 id={titleId}>Delete {target.kind === "domain" ? "domain" : "decision document"}?</h2><button className="icon-button" aria-label="Close dialog" disabled={Boolean(pending)} onClick={onClose}>×</button></div>
    <div className="dialog-content form-stack">
      <p><strong>{target.name}</strong></p>
      {preview.isPending && <p role="status">Checking documents and relationships…</p>}
      {preview.isError && <div className="error-notice" role="alert"><p>{errorMessage(preview.error)}</p><button className="button button-secondary" onClick={() => void preview.refetch()}>Reload deletion preview</button></div>}
      {details && <>
        <p>{target.kind === "domain" ? `This deletes the domain and all ${details.document_count} decision document${details.document_count === 1 ? "" : "s"} in it.` : "This removes the decision document from the Knowledge Graph."} A tombstone records when it was deleted so agents can understand references to it. Immutable audit history remains retained.</p>
        <p>{details.relationship_count} affected relationship{details.relationship_count === 1 ? "" : "s"}. {details.removed_relationship_count} graph relationship{details.removed_relationship_count === 1 ? "" : "s"} will be removed.</p>
        {details.affected_review_count > 0 && <p>{details.affected_review_count} active decision impact review{details.affected_review_count === 1 ? "" : "s"} will retain captured history but may no longer be able to read the original document.</p>}
        {details.relationship_count > 0 && <ul className="knowledge-deletion-counts">{Object.entries(details.relationships).filter(([, count]) => count > 0).map(([name, count]) => <li key={name}>{name.replaceAll("_", " ")}: <strong>{count}</strong></li>)}</ul>}
        {details.blocked_reason ? <p className="error-notice" role="alert">{details.blocked_reason}</p> : needsAcknowledgement && <label className="checkbox-label knowledge-delete-acknowledgement">
          <input type="checkbox" checked={acknowledged} disabled={Boolean(pending)} onChange={(event) => setAcknowledged(event.target.checked)} />
          <span>I understand that {target.kind === "domain" ? `all ${details.document_count} decision documents in this domain` : "this decision document"} will be deleted, {details.relationship_count} relationships will be affected ({details.removed_relationship_count} graph relationships removed), and agents may be unable to reconcile upstream decisions.</span>
        </label>}
      </>}
      {error && <div className="error-notice" role="alert"><p>{error}</p>{pending && <><p>The outcome is unconfirmed. Keep this page open and retry the saved action.</p><button className="button button-secondary" disabled={busy} onClick={() => void execute(pending)}>Retry pending deletion</button></>}</div>}
      <div className="dialog-actions"><button className="button button-secondary" disabled={Boolean(pending)} onClick={onClose}>Cancel</button><button className="button button-danger" disabled={!details || preview.isFetching || Boolean(details.blocked_reason) || Boolean(pending) || Boolean(needsAcknowledgement && !acknowledged)} onClick={remove}>{busy ? "Deleting…" : "Delete permanently"}</button></div>
    </div>
  </dialog>;
}
