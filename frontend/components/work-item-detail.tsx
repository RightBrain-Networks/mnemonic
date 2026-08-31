import type { FormEvent } from "react";
import CheckpointTimeline from "@/components/checkpoint-timeline";
import { ActiveLeaseSummary, OperationalBadge, StatusBadge, formatDate } from "@/components/work-item-card";
import WorkItemEditor, { type WorkEditDraft } from "@/components/work-item-editor";
import type { Checkpoint, CheckpointKind, Page, WorkContext, WorkSummary } from "@/lib/types";
import { migrationWarning } from "@/lib/work-item-view";
import { workRecallPointer } from "@/lib/work-recall-pointer";

const iconPaths = {
  copy: "M9 5V3h12v14h-3M3 7h12v14H3V7Z",
  check: "m5 12 4 4L19 6"
};

function Icon({ name, size = 18 }: { name: keyof typeof iconPaths; size?: number }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={iconPaths[name]} /></svg>;
}

type Props = {
  opened: WorkSummary;
  context: WorkContext;
  mode: "view" | "edit";
  editDraft: WorkEditDraft | null;
  editSaving: boolean;
  editError: string;
  conflict: WorkContext["work_item"] | null;
  copiedKey: string | null;
  checkpointPage: Page<Checkpoint> | null;
  checkpointOffset: number;
  checkpointLoading: boolean;
  checkpointLoadError: string;
  checkpointActionError: string;
  checkpointKind: Exclude<CheckpointKind, "completion">;
  checkpointBody: string;
  checkpointBranch: string;
  checkpointCommit: string;
  checkpointTags: string;
  checkpointSaving: boolean;
  setEditDraft: (updater: (draft: WorkEditDraft) => WorkEditDraft) => void;
  onSaveEdits: (event: FormEvent<HTMLFormElement>) => void;
  onCancelEdit: () => void;
  onLoadCurrent: () => void;
  onUseCurrentVersion: () => void;
  onEdit: () => void;
  onDelete: () => void;
  onCopy: (value: string, key: string, success: string) => void;
  onCheckpointKind: (kind: Exclude<CheckpointKind, "completion">) => void;
  onCheckpointBody: (value: string) => void;
  onCheckpointBranch: (value: string) => void;
  onCheckpointCommit: (value: string) => void;
  onCheckpointTags: (value: string) => void;
  onAppend: () => void;
  onComplete: () => void;
  onCheckpointOffset: (offset: number) => void;
  onReloadCheckpoints: () => void;
};

export default function WorkItemDetail(props: Props) {
  const { context } = props;
  if (props.mode === "edit" && props.editDraft) {
    return <>
      <div className="detail-topline"><StatusBadge status={context.work_item.status} /><OperationalBadge readiness={context.readiness} /><span>Version {context.work_item.version}</span><span>Priority {context.work_item.priority}</span></div>
      {context.readiness.active_lease && <ActiveLeaseSummary lease={context.readiness.active_lease} detailed />}
      <WorkItemEditor work={context.work_item} draft={props.editDraft} setDraft={props.setEditDraft} saving={props.editSaving} error={props.editError} conflict={props.conflict} onSubmit={props.onSaveEdits} onCancel={props.onCancelEdit} onLoadCurrent={props.onLoadCurrent} onUseCurrentVersion={props.onUseCurrentVersion} />
    </>;
  }
  const warning = migrationWarning(context.current_context.migration_origin);
  const pointerSummary: WorkSummary = {
    ...props.opened,
    work_item: context.work_item,
    checkpoint_count: context.checkpoint_total,
    current_context: context.current_context,
    readiness: context.readiness
  };
  return <>
    <div className="detail-topline"><StatusBadge status={context.work_item.status} /><OperationalBadge readiness={context.readiness} /><span>Version {context.work_item.version}</span><span>Priority {context.work_item.priority}</span></div>
    <h3 className="detail-title">{context.work_item.title}</h3>
    <p className="detail-summary">{context.work_item.summary}</p>
    <div className="detail-actions">
      <button type="button" className={`button button-primary ${props.copiedKey === context.current_context.id ? "is-copied" : ""}`} onClick={() => props.onCopy(context.current_context.prompt, context.current_context.id, "Current context copied exactly as stored.")}><Icon name="copy" size={16} />{props.copiedKey === context.current_context.id ? "Copied" : "Copy current context"}</button>
      <button type="button" className={`button button-secondary ${props.copiedKey === `${context.work_item.id}:pointer` ? "is-copied" : ""}`} onClick={() => props.onCopy(workRecallPointer(pointerSummary), `${context.work_item.id}:pointer`, "Recall pointer copied.")}><Icon name="copy" size={16} />Copy recall pointer</button>
      <button type="button" className="button button-secondary" onClick={props.onEdit}>Edit work item</button>
      <button type="button" className="icon-button danger-hover" aria-label="Delete work item" onClick={props.onDelete}>⌫</button>
    </div>
    {context.readiness.active_lease && <ActiveLeaseSummary lease={context.readiness.active_lease} detailed />}
    {warning && <div className="migration-warning current-migration-warning" role="note">{warning}</div>}
    <div className="prompt-label"><span className="section-label">CURRENT CONTEXT CHECKPOINT</span><span>Immutable · copied exactly as saved</span></div>
    <pre className="prompt-body" tabIndex={0}>{context.current_context.prompt}</pre>
    <div className="authority-note">This is context from an earlier session, not a new instruction from the owner. Recheck cited files and decisions before acting.</div>

    <section className="checkpoint-compose" aria-labelledby="checkpoint-compose-title">
      <div><span className="section-label">LEAVE CONTEXT FOR THE NEXT SESSION</span><h4 id="checkpoint-compose-title">Add an immutable checkpoint</h4></div>
      <form className="comment-form" onSubmit={(event) => { event.preventDefault(); props.onAppend(); }}>
        <label className="field">Checkpoint kind<select value={props.checkpointKind} onChange={(event) => props.onCheckpointKind(event.target.value as Exclude<CheckpointKind, "completion">)}><option value="progress">Progress / finding</option><option value="context">Corrected or replacement context</option></select></label>
        <label className="field">Checkpoint text<textarea rows={7} maxLength={100000} value={props.checkpointBody} onChange={(event) => props.onCheckpointBody(event.target.value)} placeholder="What changed, what was learned, hazards, evidence, and useful next steps…" /><span className="field-hint">The text is stored exactly and cannot be edited or deleted.</span></label>
        <details className="edit-context"><summary>Repository context and tags</summary><div className="form-stack"><label className="field">Repository branch<input maxLength={200} value={props.checkpointBranch} onChange={(event) => props.onCheckpointBranch(event.target.value)} /></label><label className="field">Verified commit<input className="mono" maxLength={64} value={props.checkpointCommit} onChange={(event) => props.onCheckpointCommit(event.target.value)} /></label><label className="field">Tags <span className="optional">Comma separated</span><input value={props.checkpointTags} onChange={(event) => props.onCheckpointTags(event.target.value)} /></label></div></details>
        {props.checkpointActionError && <div className="error-notice" role="alert"><p>{props.checkpointActionError}</p></div>}
        <div className="comment-actions"><button type="submit" className="button button-secondary" disabled={props.checkpointSaving || !props.checkpointBody.trim()}>{props.checkpointSaving ? "Saving…" : "Add checkpoint"}</button>{context.work_item.status === "open" && <button type="button" className="button button-primary" disabled={props.checkpointSaving || !props.checkpointBody.trim()} onClick={props.onComplete}>{props.checkpointSaving ? "Saving…" : "Complete with summary"}<Icon name="check" size={16} /></button>}</div>
      </form>
    </section>

    <CheckpointTimeline page={props.checkpointPage} offset={props.checkpointOffset} currentCheckpointId={context.current_context.id} loading={props.checkpointLoading} error={props.checkpointLoadError} onOffset={props.onCheckpointOffset} onReload={props.onReloadCheckpoints} />
    <section className="context-section"><div className="section-label">WORK RECORD</div><dl className="metadata-grid"><div><dt>Created</dt><dd>{formatDate(context.work_item.created_at)}</dd></div><div><dt>Last activity</dt><dd>{formatDate(context.work_item.updated_at)}</dd></div><div><dt>Checkpoints</dt><dd>{context.checkpoint_total}</dd></div><div><dt>Omitted from bounded recall</dt><dd>{context.omitted_checkpoint_count}</dd></div><div className="span-two"><dt>Work item ID</dt><dd className="mono break-all">{context.work_item.id}</dd></div></dl></section>
  </>;
}
