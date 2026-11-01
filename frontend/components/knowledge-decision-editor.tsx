"use client";

import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import Link from "@/components/dashboard-link";
import { ApiError, errorMessage } from "@/lib/api";
import { decisionStates, knowledgeJson, knowledgePath, type Decision, type DecisionContent, type DecisionState, type KnowledgeDomain } from "@/lib/knowledge";
import { decisionContentOptions } from "@/lib/knowledge-queries";
import { decisionWriteBody, decodeDecisionWrite, readDecisionFile } from "@/lib/knowledge-management";
import { stateLabel } from "@/components/knowledge-shared";

type Draft = { domainId: string; title: string; content: string; mediaType: Decision["media_type"]; state: DecisionState };
type Intent = { body: string; basis?: DecisionContent };
const fromDecision = (decision: DecisionContent): Draft => ({ domainId: decision.domain_id, title: decision.title, content: decision.content, mediaType: decision.media_type, state: decision.state });

export default function DecisionEditor({ projectId, domains, initialDomainId, decision, replace = false, onClose, onSaved, onPendingChange }: {
  projectId: string; domains: KnowledgeDomain[]; initialDomainId: string; decision?: Decision; replace?: boolean;
  onClose: () => void; onSaved: (decision: Decision) => void; onPendingChange: (pending: boolean) => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const [draft, setDraft] = useState<Draft>({ domainId: initialDomainId || domains[0]?.id || "", title: "", content: "", mediaType: "text/markdown", state: "proposed" });
  const [basis, setBasis] = useState<DecisionContent>();
  const [conflict, setConflict] = useState(false);
  const [fileName, setFileName] = useState("");
  const [readingFile, setReadingFile] = useState(false);
  const fileGeneration = useRef(0);
  const [pending, setPending] = useState<Intent | null>(null);
  const [busy, setBusy] = useState(false);
  const dispatching = useRef(false);
  const [error, setError] = useState("");
  const current = useQuery({ ...decisionContentOptions(projectId, decision?.decision_id ?? ""), enabled: Boolean(decision) && !pending });
  const latest = current.isError ? undefined : current.data;
  const managed = latest?.human_gate ?? decision?.human_gate;
  const uploading = !decision;

  useEffect(() => {
    const element = dialog.current!;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    element.showModal();
    return () => { fileGeneration.current += 1; element.close(); previous?.focus({ preventScroll: true }); };
  }, []);
  useEffect(() => {
    if (!latest || pending) return;
    if (!basis) { setBasis(latest); setDraft(fromDecision(latest)); }
    else if (latest.revision > basis.revision) setConflict(true);
  }, [latest, basis, pending]);

  async function chooseFile(file: File | undefined) {
    if (decision && !basis) return;
    const generation = ++fileGeneration.current;
    setFileName("");
    if (!file) { setReadingFile(false); return; }
    setReadingFile(true); setError("");
    try {
      const read = await readDecisionFile(file);
      if (generation !== fileGeneration.current) return;
      setDraft((value) => ({ ...value, content: read.content, mediaType: read.media_type, title: uploading ? read.title : value.title }));
      setFileName(file.name);
    } catch (failure) { if (generation === fileGeneration.current) setError(errorMessage(failure)); }
    finally { if (generation === fileGeneration.current) setReadingFile(false); }
  }

  async function execute(intent: Intent) {
    if (dispatching.current) return;
    dispatching.current = true; setPending(intent); setBusy(true); onPendingChange(true); setError("");
    try {
      const saved = decodeDecisionWrite(await knowledgeJson(knowledgePath(projectId, "/decisions"), { method: "PUT", body: intent.body }), projectId, intent.basis);
      setPending(null); onPendingChange(false); onSaved(saved);
    } catch (failure) {
      const definitive = failure instanceof ApiError && failure.status >= 400 && failure.status < 500 && failure.status !== 408;
      if (definitive) {
        setPending(null); onPendingChange(false);
        if (failure.status === 409 && decision) { setConflict(true); void current.refetch(); }
      }
      setError(errorMessage(failure));
    } finally { dispatching.current = false; setBusy(false); }
  }
  function save(event: FormEvent) {
    event.preventDefault();
    if (pending || managed || readingFile || conflict || decision && !basis || (uploading || replace) && !fileName) return;
    try { void execute({ body: JSON.stringify(decisionWriteBody(draft, basis)), basis }); }
    catch (failure) { setError(errorMessage(failure)); }
  }
  const disabled = Boolean(pending) || readingFile || Boolean(decision && !basis);
  const title = uploading ? "Upload decision document" : replace ? "Replace decision document" : "Edit decision document";

  return <dialog ref={dialog} className="dialog knowledge-editor-dialog" aria-labelledby={titleId} onCancel={(event) => { event.preventDefault(); if (!pending) onClose(); }}>
    <div className="dialog-header"><h2 id={titleId}>{title}</h2><button className="icon-button" aria-label="Close dialog" disabled={Boolean(pending)} onClick={onClose}>×</button></div>
    <div className="dialog-content">
      {managed ? <div className="form-stack"><p>This document records a Needs Attention question and its answer. Update it through the human decision workflow to preserve that history.</p><Link className="button button-primary" href={`/attention?project=${managed.work_project_id}&work_item_id=${managed.work_item_id}`}>Open Needs Attention</Link><button className="button button-secondary" onClick={onClose}>Close</button></div> : <form className="form-stack" onSubmit={save}>
        {decision && <p className="field-hint">{basis?.reference ?? decision.reference} · Saving appends a revision. Existing relationships are retained.</p>}
        {current.isError && decision && <div className="error-notice" role="alert"><p>{errorMessage(current.error)}</p><button className="button button-secondary" type="button" onClick={() => void current.refetch()}>Reload current document</button></div>}
        {uploading && <label className="field">Knowledge domain<select required value={draft.domainId} disabled={disabled} onChange={(event) => setDraft({ ...draft, domainId: event.target.value })}>
          <option value="" disabled>Choose a domain</option>{domains.map((domain) => <option key={domain.id} value={domain.id}>{domain.name} ({domain.prefix})</option>)}
        </select></label>}
        {(uploading || replace) && <label className="field">{replace ? "Replacement file" : "Document file"}<input type="file" accept=".md,.markdown,.txt,text/markdown,text/plain" disabled={disabled} onChange={(event) => void chooseFile(event.target.files?.[0])} /><span className="field-hint">Markdown or plain text, UTF-8, up to 1 MiB. {fileName && `Selected: ${fileName}.`}</span></label>}
        {readingFile && <p role="status">Reading document…</p>}
        <label className="field">Document title<input required maxLength={500} value={draft.title} disabled={disabled} onChange={(event) => setDraft({ ...draft, title: event.target.value })} /></label>
        <label className="field">State<select value={draft.state} disabled={disabled} onChange={(event) => setDraft({ ...draft, state: event.target.value as DecisionState })}>{decisionStates.map((state) => <option key={state} value={state}>{stateLabel(state)}</option>)}</select></label>
        {!uploading && !replace && <label className="field">Document contents<textarea className="mono knowledge-editor-content" rows={14} value={draft.content} disabled={disabled} onChange={(event) => setDraft({ ...draft, content: event.target.value })} /></label>}
        {conflict && <section className="error-notice knowledge-edit-conflict" role="alert">
          <h3>The saved document changed while you were editing.</h3>
          <p>{replace ? "Your replacement file is retained. Review the current revision and incorporate its changes into your file before replacing the document." : "Your draft is kept above. The current revision takes precedence; review it and incorporate its changes before saving again."}</p>
          {latest && basis && latest.revision > basis.revision ? <>
            <p><strong>Current revision: {latest.reference}</strong> · {latest.title} · {stateLabel(latest.state)} · {latest.author_kind === "agent" ? "Updated by an agent" : "Updated by a person"}</p>
            <label className="field">Current saved contents<textarea className="mono" readOnly rows={8} value={latest.content} /></label>
            <div className="dialog-actions"><button type="button" className="button button-secondary" disabled={Boolean(pending)} onClick={() => { setBasis(latest); setDraft(fromDecision(latest)); setConflict(false); setError(""); if (replace) setFileName(""); }}>Use current document</button>
              <button type="button" className="button button-secondary" disabled={Boolean(pending) || current.isFetching} onClick={() => { setBasis(latest); setConflict(false); setError(""); }}>I incorporated the current revision into my draft</button></div>
          </> : <button type="button" className="button button-secondary" disabled={current.isFetching} onClick={() => void current.refetch()}>Reload current revision</button>}
        </section>}
        {error && <div className="error-notice" role="alert"><p>{error}</p>{pending && <><p>The outcome is unconfirmed. Keep this page open and retry the saved action.</p><button type="button" className="button button-secondary" disabled={busy} onClick={() => void execute(pending)}>Retry pending save</button></>}</div>}
        <div className="dialog-actions"><button type="button" className="button button-secondary" disabled={Boolean(pending)} onClick={onClose}>Cancel</button><button className="button button-primary" type="submit" disabled={disabled || conflict || Boolean(decision && current.isError) || (uploading || replace) && !fileName}>{busy ? "Saving…" : uploading ? "Upload document" : replace ? "Replace document" : "Save revision"}</button></div>
      </form>}
    </div>
  </dialog>;
}
