"use client";

import Link from "@/components/dashboard-link";
import { useRouter } from "@tanstack/react-router";
import { useEffect, useRef, useState, type MouseEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { codeReviewDetailOptions } from "@/lib/work-panel-queries";
import { errorMessage } from "@/lib/api";
import { reviewStatusActions } from "@/lib/code-review-tasks";
import { taskPath, taskStatusLabels, type Task } from "@/lib/tasks";
import { validUuid } from "@/lib/wire-guards";
import { useMutationIntentRegistry } from "@/lib/mutation-intent";
import { useTaskPage } from "@/components/use-task-page";
import { useFailedReadRetry } from "@/components/use-failed-read-retry";
import { useCodeReviewActions } from "@/components/use-code-review-actions";
import TaskStatusActionButton from "@/components/task-status-action-button";
import { ReviewResult } from "@/components/code-review-panel";
import { useQueueOptions } from "@/components/work-queue-card";
import WorkTags from "@/components/work-tags";
import { formatDateTime } from "@/components/work-item-card";

type ReviewActions = ReturnType<typeof useCodeReviewActions>;
type Navigate = (event: MouseEvent<HTMLAnchorElement>) => void;

function CardActions({ task, actions, compact = true }: { task: Task; actions: ReviewActions; compact?: boolean }) {
  return <div className="queue-card-actions" onClick={(event) => event.stopPropagation()}>
    <TaskStatusActionButton subject={task} subjectLabel="code review" actions={reviewStatusActions(task)}
      deferred={task.status === "deferred"} disabled={actions.blocked || task.review_state !== "requested"}
      busy={actions.busyId === task.id} onAction={(action) => void actions.changeStatus(task, action)} compact={compact} />
    <button type="button" className={`button queue-copy-button ${actions.copiedId === task.id ? "is-copied" : ""}`}
      aria-label={`Copy recall pointer for ${task.title}`} onClick={() => void actions.copyPointer(task)}>
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" aria-hidden="true"><path d="M9 5V3h12v14h-3M3 7h12v14H3V7Z" /></svg>
      {actions.copiedId === task.id ? "Copied" : "Copy recall pointer"}
    </button>
  </div>;
}

export function CodeReviewDetailPane(props: {
  projectId: string; enabled: boolean; workId: string | null; reviewId: string;
  onNavigate: Navigate; onClose: () => void;
  onNotice: (message: string, error?: boolean) => void; onChanged: () => void;
}) {
  const actions = useCodeReviewActions(props.projectId, props.onChanged, props.onNotice);
  return <aside className="work-detail-pane code-review-detail is-open" role="region" aria-label="Code review detail">
    <ReviewDetail {...props} actions={actions} />
  </aside>;
}

export function ReviewQueueCard({ summary }: { summary: import("@/lib/types").WorkSummary }) {
  const options = useQueueOptions();
  const queryClient = useQueryClient();
  const [notice, setNotice] = useState("");
  const task = summary.review_task!;
  const actions = useCodeReviewActions(task.project_id,
    () => { void queryClient.invalidateQueries({ queryKey: ["mnemonic", "project", task.project_id] }); },
    (message) => setNotice(message));
  useEffect(() => { options.register(task.id, summary); return () => options.unregister(task.id); }, [task.id, summary, options.register, options.unregister]);
  const selected = options.selectedId === task.id;
  return <article className={`work-item-card queue-card ${selected ? "is-selected" : ""}`} role="option" tabIndex={0}
    aria-selected={selected} aria-label={task.title} data-queue-option={task.id} data-review-id={task.id}
    onClick={() => options.onSelect(summary)} onKeyDown={(event) => { if (event.target === event.currentTarget && ["Enter", " "].includes(event.key)) { event.preventDefault(); options.onSelect(summary); } }}>
    <div className="queue-card-topline"><span className={`status-badge status-${task.status}`}><span />{taskStatusLabels[task.status]}</span>
      <span className="queue-card-meta">Priority {task.priority}<span className="sep">·</span><time dateTime={task.updated_at}>{formatDateTime(task.updated_at)}</time></span></div>
    <h2 className="queue-card-title">{task.title}</h2><p className="queue-card-summary">{task.summary}</p>
    <WorkTags work={task} />
    <div className="queue-card-footer"><span className="queue-card-arrow" aria-hidden="true">→</span><CardActions task={task} actions={actions} /></div>
    {notice && <p role="status">{notice}</p>}
  </article>;
}

function ReviewDetail({ projectId, enabled, workId, reviewId, onNavigate, onClose, actions }: {
  projectId: string; enabled: boolean; workId: string | null; reviewId: string;
  onNavigate: Navigate; onClose: () => void; actions: ReviewActions;
}) {
  const registry = useMutationIntentRegistry();
  const router = useRouter();
  const valid = validUuid(workId) && validUuid(reviewId);
  const { page: taskPage, error: taskError, retry: retryTask } = useTaskPage(projectId, "all", "code_review", 0, reviewId, enabled && valid);
  const detailQuery = useQuery(codeReviewDetailOptions(projectId, workId ?? "", reviewId, enabled && valid));
  const detail = enabled && valid ? detailQuery.data : undefined;
  const error = !valid ? "This code review link is invalid." : detailQuery.error ? errorMessage(detailQuery.error) : "";
  const busy = enabled && detailQuery.isFetching;
  const retry = () => { if (enabled && valid) void detailQuery.refetch({ cancelRefetch: false }); retryTask(); };
  const closeRef = useRef<HTMLAnchorElement>(null);
  useEffect(() => {
    if (window.innerWidth > 900) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    closeRef.current?.focus();
    return () => { document.body.style.overflow = previous; };
  }, []);
  useFailedReadRetry({ scope: `${projectId}:${workId}:${reviewId}`, failed: Boolean(error), busy, retry, enabled: enabled && valid });
  const parentPath = taskPath({ kind: "work_item", id: workId ?? "", work_item_id: workId ?? "", project_id: projectId });
  const task = taskPage?.items[0];
  return <div className="detail-scroll"><div className="review-detail-content">
    <Link ref={closeRef} className="text-link" href={`/work-items?project=${projectId}`} onClick={(event) => { event.preventDefault(); onClose(); }}>← Back to work items</Link>
    {(error || taskError) && <div className="error-notice" role="alert"><p>{error || taskError}</p><button className="button button-secondary" onClick={retry}>Retry review</button></div>}
    {!detail && !error && <div className="loading-state" role="status">Loading code review…</div>}
    {detail && <>
      <div className="task-detail-heading"><span className="task-kind">Code review</span><h2 className="detail-title">{detail.source_work_state.title}</h2><p>Child task of <Link href={parentPath} onClick={onNavigate}>{detail.source_work_state.title}</Link></p></div>
      {task && <WorkTags work={task} />}
      {task && <div className="review-detail-actions"><CardActions task={task} actions={actions} /></div>}
      <ReviewResult detail={detail} statusLabel={task ? taskStatusLabels[task.status] : undefined} onOpen={(id) => { if (registry.hasDispatched()) return; void router.navigate({ href: taskPath({ kind: "work_item", id, work_item_id: id, project_id: projectId }) }); }} />
      {!detail.source_work_state.deleted && <Link className="button button-secondary" href={`${parentPath}&review=1`} onClick={onNavigate}>Open review controls</Link>}
    </>}
  </div></div>;
}
