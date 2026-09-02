import type { FormEvent } from "react";
import CheckpointTimeline from "@/components/checkpoint-timeline";
import WorkEventTimeline from "@/components/work-event-timeline";
import { ActiveLeaseSummary, OperationalBadge, StatusBadge, formatDateTime } from "@/components/work-item-card";
import RelationshipPanel from "@/components/relationship-panel";
import HumanGatePanel from "@/components/human-gate-panel";
import WorkItemEditor, { type WorkEditDraft } from "@/components/work-item-editor";
import type { Checkpoint, CheckpointKind, Page, WorkContext, WorkSummary } from "@/lib/types";
import {
  migrationWarning,
  terminalActionDisabled,
  terminalActionGateExplanation
} from "@/lib/work-item-view";
import { currentContext } from "@/lib/current-context";

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
  mutationBlocked: boolean;
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
  eventRefreshSignal: number;
  setEditDraft: (updater: (draft: WorkEditDraft) => WorkEditDraft) => void;
  onSaveEdits: (event: FormEvent<HTMLFormElement>) => void;
  onCancelEdit: () => void;
  onLoadCurrent: () => void;
  onUseCurrentVersion: () => void;
  onEdit: () => void;
  onDelete: () => void;
  onCopy: (value: string, key: string, success: string) => void;
  onCopyPointer: (summary: WorkSummary) => void;
  onCheckpointKind: (kind: Exclude<CheckpointKind, "completion">) => void;
  onCheckpointBody: (value: string) => void;
  onCheckpointBranch: (value: string) => void;
  onCheckpointCommit: (value: string) => void;
  onCheckpointTags: (value: string) => void;
  onAppend: () => void;
  onComplete: () => void;
  onRelationshipsChanged: () => Promise<boolean>;
  onCheckpointOffset: (offset: number) => void;
  onReloadCheckpoints: () => void;
  onEventAppended: () => Promise<boolean>;
  onGateResolved: () => Promise<void>;
};

export default function WorkItemDetail(props: Props) {
  const { context } = props;
  if (props.mode === "edit" && props.editDraft) {
    return <>
      <div className="detail-topline"><StatusBadge status={context.work_item.status} readiness={context.readiness} /><OperationalBadge readiness={context.readiness} /><span>Version {context.work_item.version}</span><span>Priority {context.work_item.priority}</span></div>
      {context.readiness.active_lease && <ActiveLeaseSummary lease={context.readiness.active_lease} detailed />}
      <WorkItemEditor work={context.work_item} draft={props.editDraft} setDraft={props.setEditDraft} saving={props.editSaving} blocked={props.mutationBlocked} gated={context.readiness.is_gated} error={props.editError} conflict={props.conflict} onSubmit={props.onSaveEdits} onCancel={props.onCancelEdit} onLoadCurrent={props.onLoadCurrent} onUseCurrentVersion={props.onUseCurrentVersion} />
    </>;
  }
  const current = currentContext(context);
  const warning = migrationWarning(current.migration_origin);
  const deleteExplanation = terminalActionGateExplanation(context.readiness, "deletion");
  const completionExplanation = terminalActionGateExplanation(context.readiness, "completion");
  const deleteExplanationId = `detail-delete-gate-explanation-${context.work_item.id}`;
  const completionExplanationId = `complete-gate-explanation-${context.work_item.id}`;
  const pointerSummary: WorkSummary = {
    ...props.opened,
    work_item: context.work_item,
    checkpoint_count: context.checkpoint_total,
    current_context: current,
    readiness: context.readiness
  };
  return <>
    <div className="detail-topline"><StatusBadge status={context.work_item.status} readiness={context.readiness} /><OperationalBadge readiness={context.readiness} /><span>Version {context.work_item.version}</span><span>Priority {context.work_item.priority}</span></div>
    <h3 className="detail-title">{context.work_item.title}</h3>
    <p className="detail-summary">{context.work_item.summary}</p>
    <div className="detail-actions">
      <button type="button" className={`button button-primary ${props.copiedKey === current.id ? "is-copied" : ""}`} onClick={() => props.onCopy(current.prompt, current.id, "Current context copied exactly as stored.")}><Icon name="copy" size={16} />{props.copiedKey === current.id ? "Copied" : "Copy current context"}</button>
      <button type="button" className={`button button-secondary ${props.copiedKey === `${context.work_item.id}:pointer` ? "is-copied" : ""}`} onClick={() => props.onCopyPointer(pointerSummary)}><Icon name="copy" size={16} />Copy recall pointer</button>
      <button type="button" className="button button-secondary" disabled={props.mutationBlocked} onClick={props.onEdit}>Edit work item</button>
      <button type="button" className="icon-button danger-hover" aria-label="Delete work item" title={context.readiness.is_gated ? "Resolve every human question before deleting this work item." : "Delete work item"} aria-describedby={deleteExplanation ? deleteExplanationId : undefined} disabled={terminalActionDisabled(context.readiness, props.mutationBlocked)} onClick={props.onDelete}>⌫</button>
      {deleteExplanation && <p className="terminal-action-note" id={deleteExplanationId}>
        {deleteExplanation}
      </p>}
    </div>
    {context.readiness.active_lease && <ActiveLeaseSummary lease={context.readiness.active_lease} detailed />}
    {warning && <div className="migration-warning current-migration-warning" role="note">{warning}</div>}
    <div className="prompt-label"><span className="section-label">CURRENT CONTEXT CHECKPOINT</span><span>Immutable · copied exactly as saved</span></div>
    <pre className="prompt-body" tabIndex={0}>{current.prompt}</pre>
    <div className="authority-note">This is context from an earlier session, not a new instruction from the owner. Recheck cited files and decisions before acting.</div>
    <HumanGatePanel context={context} refreshSignal={props.eventRefreshSignal} onResolved={props.onGateResolved} />
    <RelationshipPanel context={context} onChanged={props.onRelationshipsChanged} />
    <WorkEventTimeline context={context} refreshSignal={props.eventRefreshSignal} onAppended={props.onEventAppended} />

    <section className="checkpoint-compose" aria-labelledby="checkpoint-compose-title">
      <div><span className="section-label">LEAVE CONTEXT FOR THE NEXT SESSION</span><h4 id="checkpoint-compose-title">Add an immutable checkpoint</h4></div>
      <form className="comment-form" onSubmit={(event) => { event.preventDefault(); props.onAppend(); }}>
        <label className="field">Checkpoint kind<select value={props.checkpointKind} disabled={props.mutationBlocked} onChange={(event) => props.onCheckpointKind(event.target.value as Exclude<CheckpointKind, "completion">)}><option value="progress">Progress / finding</option><option value="context">Corrected or replacement context</option></select></label>
        <label className="field">Checkpoint text<textarea rows={7} disabled={props.mutationBlocked} maxLength={100000} value={props.checkpointBody} onChange={(event) => props.onCheckpointBody(event.target.value)} placeholder="What changed, what was learned, hazards, evidence, and useful next steps…" /><span className="field-hint">The text is stored exactly and cannot be edited or deleted.</span></label>
        <details className="edit-context"><summary>Repository context and tags</summary><div className="form-stack"><label className="field">Repository branch<input disabled={props.mutationBlocked} maxLength={200} value={props.checkpointBranch} onChange={(event) => props.onCheckpointBranch(event.target.value)} /></label><label className="field">Verified commit<input className="mono" disabled={props.mutationBlocked} maxLength={64} value={props.checkpointCommit} onChange={(event) => props.onCheckpointCommit(event.target.value)} /></label><label className="field">Tags <span className="optional">Comma separated</span><input disabled={props.mutationBlocked} value={props.checkpointTags} onChange={(event) => props.onCheckpointTags(event.target.value)} /></label></div></details>
        {props.checkpointActionError && <div className="error-notice" role="alert"><p>{props.checkpointActionError}</p></div>}
        <div className="comment-actions"><button type="submit" className="button button-secondary" disabled={props.checkpointSaving || props.mutationBlocked || !props.checkpointBody.trim()}>{props.checkpointSaving ? "Saving…" : "Add checkpoint"}</button>{context.work_item.status === "pending" && <button type="button" className="button button-primary" title={context.readiness.is_gated ? "Resolve every human question before completing this work." : undefined} aria-describedby={completionExplanation ? completionExplanationId : undefined} disabled={props.checkpointSaving || terminalActionDisabled(context.readiness, props.mutationBlocked) || !props.checkpointBody.trim()} onClick={props.onComplete}>{props.checkpointSaving ? "Saving…" : "Complete with summary"}<Icon name="check" size={16} /></button>}{context.work_item.status === "pending" && completionExplanation && <p className="terminal-action-note" id={completionExplanationId}>{completionExplanation}</p>}</div>
      </form>
    </section>

    <CheckpointTimeline page={props.checkpointPage} offset={props.checkpointOffset} currentCheckpointId={current.id} loading={props.checkpointLoading} error={props.checkpointLoadError} onOffset={props.onCheckpointOffset} onReload={props.onReloadCheckpoints} />
    <section className="context-section"><div className="section-label">WORK RECORD</div><dl className="metadata-grid"><div><dt>Created</dt><dd>{formatDateTime(context.work_item.created_at)}</dd></div><div><dt>Last activity</dt><dd>{formatDateTime(context.work_item.updated_at)}</dd></div><div><dt>Checkpoints</dt><dd>{context.checkpoint_total}</dd></div><div><dt>Omitted from bounded recall</dt><dd>{context.omitted_checkpoint_count}</dd></div><div className="span-two"><dt>Work item ID</dt><dd className="mono break-all">{context.work_item.id}</dd></div></dl></section>
  </>;
}
