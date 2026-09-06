import ExternalReferencesEditor from "@/components/external-references-editor";
import ExternalReferences from "@/components/external-references";
import JobReportEditor from "@/components/job-report-editor";
import type { JobReportDraft } from "@/lib/job-completion-reports";
import type { FormEvent } from "react";
import type { WorkItem, WorkStatus, ExternalReference } from "@/lib/types";
import { statusLabels } from "@/components/work-item-card";
import { editableLifecycleStatuses } from "@/lib/work-item-view";

export type WorkEditDraft = {
  externalReferences: ExternalReference[];
  title: string;
  summary: string;
  priority: number;
  status: WorkStatus;
};

export function draftFromWork(work: WorkItem): WorkEditDraft {
  return { externalReferences: (work.external_references ?? []).map((item) => ({ ...item })), title: work.title, summary: work.summary, priority: work.priority, status: work.status };
}

type Props = {
  jobReportDraft: JobReportDraft;
  onJobReportDraft: (draft: JobReportDraft) => void;
  work: WorkItem;
  draft: WorkEditDraft;
  setDraft: (updater: (draft: WorkEditDraft) => WorkEditDraft) => void;
  saving: boolean;
  blocked: boolean;
  gated: boolean;
  error: string;
  conflict: WorkItem | null;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  onCancel: () => void;
  onLoadCurrent: () => void;
  onUseCurrentVersion: () => void;
};

export default function WorkItemEditor({
  jobReportDraft, onJobReportDraft,
  work,
  draft,
  setDraft,
  saving,
  blocked,
  gated,
  error,
  conflict,
  onSubmit,
  onCancel,
  onLoadCurrent,
  onUseCurrentVersion
}: Props) {
  const lifecycleOptions = editableLifecycleStatuses(work.status).filter((status) => (
    !gated || status === work.status || status === "pending"
  ));
  const requiresReport = work.status === "pending"
    && (draft.status === "wont-do" || draft.status === "promoted");
  return <form className="form-stack edit-form" onSubmit={onSubmit}>
    <p className="dialog-intro">Edit the durable objective. Existing checkpoint text and provenance cannot be changed.</p>
    <label className="field">Title<input required disabled={blocked} maxLength={200} value={draft.title} onChange={(event) => setDraft((value) => ({ ...value, title: event.target.value }))} /></label>
    <label className="field">Summary<textarea required disabled={blocked} rows={4} maxLength={1000} value={draft.summary} onChange={(event) => setDraft((value) => ({ ...value, summary: event.target.value }))} /></label>
    <label className="field field-half">Priority<input type="number" disabled={blocked} min={0} max={100} value={draft.priority} onChange={(event) => setDraft((value) => ({ ...value, priority: Number(event.target.value) }))} /><span className="field-hint">0–100. Higher values are more important; ordinary search is not a scheduler.</span></label>
    <label className="field field-half">Lifecycle<select value={draft.status} disabled={blocked} onChange={(event) => setDraft((value) => ({ ...value, status: event.target.value as WorkStatus }))}>
      {lifecycleOptions.map((status) => <option value={status} key={status}>{statusLabels[status]}</option>)}
    </select><span className="field-hint">{gated
      ? "Terminal lifecycle changes stay unavailable until every human question is resolved. Nonterminal fields remain editable."
      : work.status === "pending" ? "Done is available only through the completion workflow. Use the card’s Defer action to hold work out of the queue." : work.status === "deferred" ? "Only a human can defer work. Moving it to Pending restores that lifecycle, but blockers or human gates can still keep it out of ready discovery." : `${statusLabels[work.status]} work can only remain there or reopen as Pending.`}</span></label>
    <ExternalReferencesEditor value={draft.externalReferences} disabled={blocked || saving} onChange={(externalReferences) => setDraft((value) => ({ ...value, externalReferences }))} />
    {requiresReport && <JobReportEditor projectId={work.project_id} draft={jobReportDraft} onChange={onJobReportDraft} disabled={blocked || saving} />}
    {error && <div className="error-notice" role="alert"><p>{error}</p>{!conflict && <button type="button" className="button button-secondary" onClick={onLoadCurrent}>Load current version</button>}</div>}
    {conflict && <section className="conflict-panel"><h3>Current saved version · v{conflict.version}</h3><pre>{JSON.stringify({ title: conflict.title, summary: conflict.summary, priority: conflict.priority, status: conflict.status }, null, 2)}</pre><h4>Current external references</h4><ExternalReferences references={conflict.external_references} />{!conflict.external_references?.length && <p>No references</p>}<h4>Attempted external references</h4><ExternalReferences references={draft.externalReferences} />{!draft.externalReferences.length && <p>No references (clear)</p>}<p>Reconcile your ordered replacement with the current list before resubmitting. Nothing is combined automatically.</p><button type="button" className="button button-secondary" disabled={blocked} onClick={onUseCurrentVersion}>Use my reconciled edits on version {conflict.version}</button></section>}
    <div className="dialog-actions sticky-actions">
      <span className="version-note">Editing version {work.version}</span>
      <button type="button" className="button button-secondary" disabled={saving || blocked} onClick={onCancel}>Cancel</button>
      <button type="submit" className="button button-primary" disabled={saving || blocked || Boolean(conflict) || requiresReport && jobReportDraft.promptRevision === null}>{saving ? "Saving…" : "Save changes"}</button>
    </div>
  </form>;
}
