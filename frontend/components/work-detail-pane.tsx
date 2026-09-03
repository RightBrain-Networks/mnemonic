"use client";

import {
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type FormEvent,
  type ReactNode
} from "react";
import CheckpointTimeline from "@/components/checkpoint-timeline";
import HumanGatePanel from "@/components/human-gate-panel";
import RelationshipPanel from "@/components/relationship-panel";
import WorkEventTimeline from "@/components/work-event-timeline";
import {
  ActiveLeaseSummary,
  OperationalBadge,
  StatusBadge,
  clientLabel,
  formatDateTime
} from "@/components/work-item-card";
import WorkItemEditor, { type WorkEditDraft } from "@/components/work-item-editor";
import WorkMergePanel from "@/components/work-merge-panel";
import { currentContext } from "@/lib/current-context";
import type {
  Checkpoint,
  CheckpointKind,
  Page,
  WorkContext,
  WorkItem,
  WorkMergeResult,
  WorkSummary
} from "@/lib/types";
import { copyKey, detailTabs, type DetailTab } from "@/lib/work-detail-tabs";
import {
  migrationWarning,
  terminalActionDisabled,
  terminalActionGateExplanation
} from "@/lib/work-item-view";

const iconPaths = {
  copy: "M9 5V3h12v14h-3M3 7h12v14H3V7Z",
  check: "m5 12 4 4L19 6",
  close: "m6 6 12 12M6 18 18 6",
  back: "M19 12H5m5-5-5 5 5 5",
  library: "M3 3h6v18H3V3Zm10 0h4l4 17-4 1-4-18Z"
};

function Icon({ name, size = 18 }: { name: keyof typeof iconPaths; size?: number }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={iconPaths[name]} /></svg>;
}

// The slide-in starts offset and transparent with no transition, then eases in
// on the next painted frame. The reduced-motion rule in globals.css collapses
// the durations with !important, so these inline values never override it.
const SLIDE_START: CSSProperties = {
  opacity: 0,
  transform: "translateX(28px)",
  transition: "none",
  willChange: "opacity, transform"
};

const SLIDE_END: CSSProperties = {
  opacity: 1,
  transform: "none",
  transition: "opacity 360ms cubic-bezier(.25,1,.5,1), transform 420ms cubic-bezier(.25,1,.5,1)",
  willChange: "opacity, transform"
};

export type WorkDetailPaneProps = {
  opened: WorkSummary | null;
  context: WorkContext | null;
  contextLoading: boolean;
  contextError: string;
  reconciliationRequired: boolean;
  onRetryContext: () => void;
  tab: DetailTab;
  onTab: (tab: DetailTab) => void;
  mode: "view" | "edit";
  editDraft: WorkEditDraft | null;
  editSaving: boolean;
  mutationBlocked: boolean;
  editError: string;
  conflict: WorkItem | null;
  setEditDraft: (updater: (draft: WorkEditDraft) => WorkEditDraft) => void;
  onSaveEdits: (event: FormEvent<HTMLFormElement>) => void;
  onCancelEdit: () => void;
  onLoadCurrent: () => void;
  onUseCurrentVersion: () => void;
  onEdit: () => void;
  mergeOpen: boolean;
  onOpenMerge: () => void;
  onCloseMerge: () => void;
  onMerged: (result: WorkMergeResult) => void | Promise<void>;
  onMergeSourceChanged: () => void | Promise<void>;
  onDelete: () => void;
  onDefer: (summary: WorkSummary) => void;
  deferring: boolean;
  onOpenCanonical: (workItemId: string) => void;
  onViewDuplicateGroup: (canonicalWorkItemId: string) => void;
  onCopy: (value: string, key: string, success: string) => void;
  onCopyPointer: (summary: WorkSummary) => void;
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
  eventRefreshSignal: number;
  recovery?: ReactNode;
  notice?: ReactNode;
  onBack: () => void;
  backDisabled: boolean;
};

function ContextRetry({ message, onRetry }: { message: string; onRetry: () => void }) {
  return <div className="error-notice" role="alert"><p>{message}</p><button type="button" className="button button-secondary" onClick={onRetry}>Try again</button></div>;
}

function EmptyPane() {
  return <div className="detail-empty">
    <div className="empty-art"><Icon name="library" size={34} /><span /></div>
    <span className="eyebrow">WORK CONTEXT</span>
    <h2>Pick a work item.</h2>
    <p>Its current context, checkpoint history, work graph, human questions, and activity open here — nothing pops over the queue.</p>
    <p className="detail-empty-hint"><kbd>↑</kbd><kbd>↓</kbd>move the selection</p>
  </div>;
}

function DuplicateAuditPanel({
  context,
  copiedKey,
  onCopy,
  onOpenCanonical,
  onViewDuplicateGroup
}: Pick<WorkDetailPaneProps, "copiedKey" | "onCopy" | "onOpenCanonical" | "onViewDuplicateGroup"> & { context: WorkContext }) {
  const auditKey = copyKey(context.work_item.id, "audit-id");
  const mergeEvent = context.recent_events.find((event) => (
    event.event_type === "work_merged"
    && "role" in event.metadata
    && event.metadata.role === "source"
  ));
  return <section className="duplicate-audit-panel" aria-labelledby="duplicate-audit-title">
    <div>
      <span className="section-label">PERMANENT DUPLICATE AUDIT</span>
      <h4 id="duplicate-audit-title">Canonical direction</h4>
    </div>
    <dl className="duplicate-direction-grid">
      <div><dt>Direct destination</dt><dd><bdi dir="auto">{context.canonical.direct_destination?.title}</bdi><span className="mono break-all">{context.canonical.direct_destination?.id}</span></dd></div>
      <div><dt>Current canonical root</dt><dd><bdi dir="auto">{context.canonical.canonical_work_item.title}</bdi><span className="mono break-all">{context.canonical.canonical_work_item.id}</span></dd></div>
    </dl>
    {context.canonical.path.length > 1 && <ol className="duplicate-hop-path" aria-label="Canonical merge path">{context.canonical.path.map((hop) => <li key={hop.id}><bdi dir="auto">{hop.title}</bdi><span className="mono break-all">{hop.id}</span></li>)}</ol>}
    <p>{context.duplicate_member_total} immutable duplicate audit record{context.duplicate_member_total === 1 ? "" : "s"} in this canonical group{context.omitted_duplicate_member_count ? `; ${context.omitted_duplicate_member_count} omitted from this bounded view` : ""}.</p>
    {mergeEvent && <dl className="duplicate-merge-fact">
      <div><dt>Merge rationale</dt><dd>{mergeEvent.body}</dd></div>
      <div><dt>Recorded</dt><dd>{formatDateTime(mergeEvent.created_at)}</dd></div>
      <div><dt>Provenance</dt><dd>{mergeEvent.actor_client} · {mergeEvent.actor_session_id}{mergeEvent.actor_model ? ` · ${mergeEvent.actor_model}` : ""}</dd></div>
    </dl>}
    <div className="duplicate-audit-actions">
      <button type="button" className={`button button-secondary ${copiedKey === auditKey ? "is-copied" : ""}`} onClick={() => onCopy(context.work_item.id, auditKey, "Duplicate audit ID copied.")}><Icon name="copy" size={16} />Copy audit ID</button>
      <button type="button" className="button button-primary" onClick={() => onOpenCanonical(context.canonical.canonical_work_item.id)}>Open canonical work</button>
      <button type="button" className="button button-secondary" onClick={() => onViewDuplicateGroup(context.canonical.canonical_work_item.id)}>View duplicate group</button>
    </div>
  </section>;
}

function ContextTab({ context, isDuplicate, props }: { context: WorkContext; isDuplicate: boolean; props: WorkDetailPaneProps }) {
  if (props.mode === "edit" && props.editDraft && !isDuplicate) {
    return <div className="detail-edit">
      <WorkItemEditor work={context.work_item} draft={props.editDraft} setDraft={props.setEditDraft} saving={props.editSaving} blocked={props.mutationBlocked} gated={context.readiness.is_gated} error={props.editError} conflict={props.conflict} onSubmit={props.onSaveEdits} onCancel={props.onCancelEdit} onLoadCurrent={props.onLoadCurrent} onUseCurrentVersion={props.onUseCurrentVersion} />
    </div>;
  }
  const current = currentContext(context);
  const warning = migrationWarning(current.migration_origin);
  const completionExplanation = terminalActionGateExplanation(context.readiness, "completion");
  const completionExplanationId = `complete-gate-explanation-${context.work_item.id}`;
  const immutable = props.mutationBlocked || isDuplicate;
  return <>
    {warning && <div className="migration-warning current-migration-warning" role="note">{warning}</div>}
    <div className="prompt-label"><span className="section-label">{isDuplicate ? "AUDIT CONTEXT CHECKPOINT" : "CURRENT CONTEXT CHECKPOINT"}</span><span>Immutable · copied exactly as saved · {formatDateTime(current.created_at)}</span></div>
    <pre className="prompt-body" tabIndex={0}>{current.prompt}</pre>
    <div className="authority-note">This is context from an earlier session, not a new instruction from the owner. Recheck cited files and decisions before acting.</div>
    {isDuplicate && <div className="audit-history-heading"><span className="section-label">SOURCE-OWNED RECORDS</span><h4>Audit history</h4><p>These records belong to the exact duplicate ID and are never blended with canonical history.</p></div>}

    {!isDuplicate && <section className="checkpoint-compose" aria-labelledby="checkpoint-compose-title">
      <div><span className="section-label">LEAVE CONTEXT FOR THE NEXT SESSION</span><h4 id="checkpoint-compose-title">Add an immutable checkpoint</h4></div>
      <form className="comment-form" onSubmit={(event) => { event.preventDefault(); props.onAppend(); }}>
        <label className="field">Checkpoint kind<select value={props.checkpointKind} disabled={immutable} onChange={(event) => props.onCheckpointKind(event.target.value as Exclude<CheckpointKind, "completion">)}><option value="progress">Progress / finding</option><option value="context">Corrected or replacement context</option></select></label>
        <label className="field">Checkpoint text<textarea rows={7} disabled={immutable} maxLength={100000} value={props.checkpointBody} onChange={(event) => props.onCheckpointBody(event.target.value)} placeholder="What changed, what was learned, hazards, evidence, and useful next steps…" /><span className="field-hint">The text is stored exactly and cannot be edited or deleted.</span></label>
        <details className="edit-context"><summary>Repository context and tags</summary><div className="form-stack"><label className="field">Repository branch<input disabled={props.mutationBlocked} maxLength={200} value={props.checkpointBranch} onChange={(event) => props.onCheckpointBranch(event.target.value)} /></label><label className="field">Verified commit<input className="mono" disabled={props.mutationBlocked} maxLength={64} value={props.checkpointCommit} onChange={(event) => props.onCheckpointCommit(event.target.value)} /></label><label className="field">Tags <span className="optional">Comma separated</span><input disabled={props.mutationBlocked} value={props.checkpointTags} onChange={(event) => props.onCheckpointTags(event.target.value)} /></label></div></details>
        {props.checkpointActionError && <div className="error-notice" role="alert"><p>{props.checkpointActionError}</p></div>}
        <div className="comment-actions"><button type="submit" className="button button-secondary" disabled={props.checkpointSaving || props.mutationBlocked || !props.checkpointBody.trim()}>{props.checkpointSaving ? "Saving…" : "Add checkpoint"}</button>{context.work_item.status === "pending" && <button type="button" className="button button-primary" title={context.readiness.is_gated ? "Resolve every human question before completing this work." : undefined} aria-describedby={completionExplanation ? completionExplanationId : undefined} disabled={props.checkpointSaving || terminalActionDisabled(context.readiness, props.mutationBlocked) || !props.checkpointBody.trim()} onClick={props.onComplete}>{props.checkpointSaving ? "Saving…" : "Complete with summary"}<Icon name="check" size={16} /></button>}{context.work_item.status === "pending" && completionExplanation && <p className="terminal-action-note" id={completionExplanationId}>{completionExplanation}</p>}</div>
      </form>
    </section>}
  </>;
}

function TabBody({ context, isDuplicate, props }: { context: WorkContext; isDuplicate: boolean; props: WorkDetailPaneProps }) {
  switch (props.tab) {
    case "context":
      return <ContextTab context={context} isDuplicate={isDuplicate} props={props} />;
    case "history":
      return <>
        <CheckpointTimeline page={props.checkpointPage} offset={props.checkpointOffset} currentCheckpointId={currentContext(context).id} loading={props.checkpointLoading} error={props.checkpointLoadError} onOffset={props.onCheckpointOffset} onReload={props.onReloadCheckpoints} />
        <section className="context-section"><div className="section-label">WORK RECORD</div><dl className="metadata-grid"><div><dt>Created</dt><dd>{formatDateTime(context.work_item.created_at)}</dd></div><div><dt>Last activity</dt><dd>{formatDateTime(context.work_item.updated_at)}</dd></div><div><dt>Checkpoints</dt><dd>{context.checkpoint_total}</dd></div><div><dt>Omitted from bounded recall</dt><dd>{context.omitted_checkpoint_count}</dd></div><div className="span-two"><dt>Work item ID</dt><dd className="mono break-all">{context.work_item.id}</dd></div></dl></section>
      </>;
    case "graph":
      return <>
        {/* Merge mode only opens on a canonical root, but a lost merge response followed by a
            live-sync reload can turn the open source into a duplicate while its merge intent is
            still unresolved. The panel stays mounted so its recovery block and exact retry survive. */}
        {props.mergeOpen && <WorkMergePanel source={context} onClose={props.onCloseMerge} onMerged={props.onMerged} onSourceChanged={props.onMergeSourceChanged} />}
        <RelationshipPanel context={context} onChanged={props.onRelationshipsChanged} />
      </>;
    case "questions":
      return <HumanGatePanel context={context} refreshSignal={props.eventRefreshSignal} onResolved={props.onGateResolved} />;
    case "activity":
      return <WorkEventTimeline context={context} refreshSignal={props.eventRefreshSignal} onAppended={props.onEventAppended} />;
  }
}

function OpenedPane({ opened, props }: { opened: WorkSummary; props: WorkDetailPaneProps }) {
  // A context is only trusted for the header when it belongs to the selection;
  // the dashboard sets both together, so a mismatch renders as still loading.
  const context = props.context && props.context.work_item.id === opened.work_item.id
    ? props.context
    : null;
  const work = context?.work_item ?? opened.work_item;
  const readiness = context?.readiness ?? opened.readiness;
  const current = context ? currentContext(context) : opened.current_context;
  const isDuplicate = context ? context.canonical.is_duplicate : opened.readiness.is_duplicate;
  const pointerSummary: WorkSummary = context
    ? {
      ...opened,
      work_item: context.work_item,
      checkpoint_count: context.checkpoint_total,
      current_context: currentContext(context),
      readiness: context.readiness
    }
    : opened;
  const idKey = copyKey(work.id, "id");
  const pointerKey = copyKey(work.id, "pointer");
  const contextKey = copyKey(work.id, "context");
  const canonicalKey = copyKey(work.id, "canonical-id");
  const idCopied = props.copiedKey === idKey;
  const deleteExplanation = terminalActionGateExplanation(readiness, "deletion");
  const deleteExplanationId = `detail-delete-gate-explanation-${work.id}`;
  const mergeLeaseExplanation = context?.duplicate_merge_eligibility.source_lease_state === "active"
    ? "Release the source’s active lease, or wait for it to expire, before merging in the browser."
    : "";
  const mergeLeaseExplanationId = `merge-lease-explanation-${work.id}`;
  const actionsLocked = !context || props.mutationBlocked;
  const deferrable = work.status === "pending" || work.status === "deferred";
  const reconciling = props.reconciliationRequired && props.contextLoading;
  const tabs = detailTabs(context, opened);

  return <>
    <div className="detail-header">
      {props.recovery}
      {props.notice}
      <div className="detail-identity">
        <button type="button" className="icon-button detail-back" aria-label="Back to work queue" disabled={props.backDisabled} onClick={props.onBack}><Icon name="back" /></button>
        <StatusBadge status={work.status} readiness={readiness} />
        <OperationalBadge readiness={readiness} />
        <span className="detail-version" title="Work item version">v{work.version}</span>
        <span className="detail-id"><code>{work.id}</code><button type="button" className={`icon-button detail-copy-id ${idCopied ? "is-copied" : ""}`} aria-label="Copy work item ID" title={idCopied ? "Copied" : "Copy work item ID"} onClick={() => props.onCopy(work.id, idKey, `Work item ID copied: ${work.id}`)}><Icon name={idCopied ? "check" : "copy"} size={13} /></button></span>
        <span className="detail-activity">Last activity <time dateTime={work.updated_at}>{formatDateTime(work.updated_at)}</time></span>
      </div>
      <h3 className="detail-title">{work.title}</h3>
      <p className="detail-summary">{work.summary}</p>
      <dl className="detail-facts" aria-label="Work item facts">
        <div><dt>Priority</dt><dd>{work.priority}</dd></div>
        <div><dt>Checkpoints</dt><dd>{context?.checkpoint_total ?? opened.checkpoint_count}</dd></div>
        <div><dt>Current context</dt><dd>{clientLabel(current.source_client)}</dd></div>
        <div><dt>Session</dt><dd className="mono" title={current.source_session_id}>{current.source_session_id}</dd></div>
        <div className="detail-fact-tags"><dt>Tags</dt><dd>{current.tags.length ? current.tags.map((tag) => <span className="tag" key={tag}>{tag}</span>) : <span className="detail-fact-none">None</span>}</dd></div>
      </dl>
      {reconciling && <div className="detail-reconciliation-status" role="status"><span className="spinner" />Reconciling saved work context…</div>}
      {context && isDuplicate && <DuplicateAuditPanel context={context} copiedKey={props.copiedKey} onCopy={props.onCopy} onOpenCanonical={props.onOpenCanonical} onViewDuplicateGroup={props.onViewDuplicateGroup} />}
      {readiness.active_lease && <ActiveLeaseSummary lease={readiness.active_lease} detailed />}
      <div className="detail-actions">
        <button type="button" className={`button button-primary ${props.copiedKey === pointerKey ? "is-copied" : ""}`} onClick={() => props.onCopyPointer(pointerSummary)}><Icon name="copy" size={16} />{props.copiedKey === pointerKey ? "Copied" : "Copy recall pointer"}</button>
        <button type="button" className={`button copy-button detail-copy-context ${props.copiedKey === contextKey ? "is-copied" : ""}`} aria-label="Copy current context" disabled={!context} onClick={() => { if (context) props.onCopy(currentContext(context).prompt, contextKey, "Current context copied exactly as stored."); }}><Icon name="copy" size={16} />{props.copiedKey === contextKey ? "Copied" : "Copy context"}</button>
        {!isDuplicate && <button type="button" className="button button-secondary" aria-label="Edit work item" disabled={actionsLocked} onClick={props.onEdit}>Edit</button>}
        {!isDuplicate && <button type="button" className="button button-secondary" title={mergeLeaseExplanation || undefined} aria-describedby={mergeLeaseExplanation ? mergeLeaseExplanationId : undefined} disabled={actionsLocked || Boolean(mergeLeaseExplanation)} onClick={props.onOpenMerge}>Merge as duplicate…</button>}
        {!isDuplicate && deferrable && <button
          className="button defer-button"
          type="button"
          disabled={actionsLocked || props.deferring || readiness.has_active_lease || readiness.is_duplicate}
          aria-label={work.status === "deferred" ? `Move ${work.title} to Pending` : `Defer ${work.title}`}
          title={readiness.is_duplicate
            ? "Duplicate audit records are immutable. Open it to navigate to canonical work."
            : readiness.has_active_lease
            ? "Active work cannot be deferred until its lease is released or expires."
            : work.status === "deferred"
              ? "Move this work item back to Pending; blockers and human gates still apply"
              : "Hold this work item out of the work queue"}
          onClick={() => props.onDefer(pointerSummary)}
        >{props.deferring ? "Saving…" : work.status === "deferred" ? "Move to Pending" : "Defer"}</button>}
        {!isDuplicate && context && context.duplicate_member_total > 0 && <button type="button" className={`button button-secondary ${props.copiedKey === canonicalKey ? "is-copied" : ""}`} onClick={() => props.onCopy(work.id, canonicalKey, "Canonical work ID copied.")}><Icon name="copy" size={16} />Copy canonical ID</button>}
        {!isDuplicate && <button type="button" className="button detail-delete" aria-label="Delete work item" title={readiness.is_gated ? "Resolve every human question before deleting this work item." : "Delete work item"} aria-describedby={deleteExplanation ? deleteExplanationId : undefined} disabled={!context || terminalActionDisabled(readiness, props.mutationBlocked)} onClick={props.onDelete}>Delete</button>}
        {!isDuplicate && deleteExplanation && <p className="terminal-action-note" id={deleteExplanationId}>
          {deleteExplanation}
        </p>}
        {mergeLeaseExplanation && <p className="terminal-action-note" id={mergeLeaseExplanationId}>{mergeLeaseExplanation}</p>}
      </div>
    </div>
    <div className="detail-tabs" role="tablist" aria-label="Work context sections">
      {tabs.map((tab) => {
        const selected = props.tab === tab.key;
        return <button type="button" role="tab" key={tab.key} id={`detail-tab-${tab.key}`} aria-selected={selected} aria-controls={`detail-panel-${tab.key}`} className={`detail-tab ${selected ? "is-selected" : ""}`} onClick={() => props.onTab(tab.key)}>{tab.label}{tab.count !== undefined && <span className={`detail-tab-count ${tab.alert ? "is-alert" : ""}`}>{tab.count}</span>}</button>;
      })}
    </div>
    <div className="detail-tab-body" role="tabpanel" id={`detail-panel-${props.tab}`} aria-labelledby={`detail-tab-${props.tab}`}>
      {!context
        ? props.contextError
          ? <ContextRetry message={props.contextError} onRetry={props.onRetryContext} />
          : props.contextLoading || !props.reconciliationRequired
            ? <div className="loading-state detail-loading" role="status"><span className="spinner" />{props.reconciliationRequired ? "Reconciling saved work context…" : "Recalling work context…"}</div>
            : <ContextRetry message="The saved mutation could not be reconciled with current work context." onRetry={props.onRetryContext} />
        : <div className="detail-reconciliation-frame" key={work.id} aria-busy={reconciling} inert={reconciling}>
          {props.contextError && <ContextRetry message={props.contextError} onRetry={props.onRetryContext} />}
          <TabBody context={context} isDuplicate={isDuplicate} props={props} />
        </div>}
    </div>
  </>;
}

export default function WorkDetailPane(props: WorkDetailPaneProps) {
  const { opened } = props;
  const openedId = opened?.work_item.id ?? null;
  const [motion, setMotion] = useState<CSSProperties>(SLIDE_END);
  const scrollRef = useRef<HTMLDivElement>(null);
  const shownId = useRef<string | null>(null);

  // Replays only when the selected work item changes; reloading the same item
  // (live sync, reconciliation) keeps the pane where it is.
  useLayoutEffect(() => {
    const previous = shownId.current;
    if (openedId === previous) return;
    shownId.current = openedId;
    if (openedId === null) return;
    if (scrollRef.current) scrollRef.current.scrollTop = 0;
    setMotion(SLIDE_START);
    let second = 0;
    const first = requestAnimationFrame(() => {
      second = requestAnimationFrame(() => setMotion(SLIDE_END));
    });
    return () => {
      cancelAnimationFrame(first);
      cancelAnimationFrame(second);
      // Restoring the prior id keeps a cancelled run replayable, so a
      // strict-mode double invocation still finishes at the resting state.
      shownId.current = previous;
    };
  }, [openedId]);

  return <section className={`work-detail-pane ${opened ? "is-open" : ""}`} aria-label="Work context" aria-live="polite">
    {opened
      ? <div className="detail-scroll" ref={scrollRef}>
        <div className="detail-motion" style={motion}>
          <OpenedPane opened={opened} props={props} />
        </div>
      </div>
      : <EmptyPane />}
  </section>;
}
