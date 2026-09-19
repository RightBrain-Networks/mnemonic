"use client";

import Link from "next/link";
import { useEffect, useRef, useState, type MouseEvent } from "react";
import { api, errorMessage } from "@/lib/api";
import { decodeCodeReviewDetail, type CodeReviewDetail } from "@/lib/code-reviews";
import { reviewStatusActions, reviewStatusFilters } from "@/lib/code-review-tasks";
import { taskPath, taskStatusLabels, type Task, type TaskStatus } from "@/lib/tasks";
import { validUuid } from "@/lib/wire-guards";
import { dialogOpen, typingTarget } from "@/lib/keyboard-shortcuts";
import { listScrollTopFor } from "@/lib/work-queue";
import { WORK_SPLIT_MIN, WORK_SPLIT_MAX } from "@/lib/work-split";
import { useMutationIntentRegistry } from "@/lib/mutation-intent";
import { TaskPagination } from "@/components/task-cards";
import { useTaskPage } from "@/components/use-task-page";
import { useFailedReadRetry } from "@/components/use-failed-read-retry";
import { useWorkSplit } from "@/components/use-work-split";
import { useCodeReviewActions } from "@/components/use-code-review-actions";
import TaskStatusActionButton from "@/components/task-status-action-button";
import { ReviewResult } from "@/components/code-review-panel";
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

export default function CodeReviewLibrary({ projectId, refreshSignal, workId, reviewId, onNavigate, onNotice, onChanged }: {
  projectId: string; refreshSignal: number; workId: string | null; reviewId: string | null;
  onNavigate: Navigate; onNotice: (message: string, error?: boolean) => void; onChanged: () => void;
}) {
  const [status, setStatus] = useState<TaskStatus | "all">("pending");
  const [offset, setOffset] = useState(0);
  const [scrolled, setScrolled] = useState(false);
  const listRef = useRef<HTMLDivElement>(null);
  const registry = useMutationIntentRegistry();
  const split = useWorkSplit<HTMLElement>();
  const { page, error, busy, retry } = useTaskPage(projectId, refreshSignal, status, "code_review", offset);
  const actions = useCodeReviewActions(projectId, onChanged, onNotice);

  function select(task: Task | null) {
    if (registry.hasDispatched()) { onNotice("Resolve the pending mutation before selecting another review.", true); return; }
    const href = task ? taskPath(task) : `/code-reviews?project=${projectId}`;
    window.history.replaceState(null, "", href);
    if (!task) requestAnimationFrame(() => listRef.current?.querySelector<HTMLElement>(`[data-review-id="${reviewId}"]`)?.focus({ preventScroll: true }));
  }
  function filter(next: TaskStatus | "all") {
    if (registry.hasDispatched()) return;
    setStatus(next); setOffset(0); select(null);
    if (listRef.current) listRef.current.scrollTop = 0;
  }

  useEffect(() => {
    function shortcut(event: KeyboardEvent) {
      if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey || typingTarget(event.target) || dialogOpen()) return;
      if (event.target instanceof Element && event.target.closest(".status-split-button, [role=menu], [role=separator]")) return;
      if (event.key === "Escape") { select(null); return; }
      if (event.key.toLowerCase() === "c") {
        if (event.target instanceof Element && event.target.closest(".work-detail-pane")) return;
        const task = page?.items.find((item) => item.id === reviewId);
        if (task) { event.preventDefault(); void actions.copyPointer(task); }
        return;
      }
      if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
        event.preventDefault();
        const index = reviewStatusFilters.findIndex((value) => value === status);
        filter(reviewStatusFilters[(index + (event.key === "ArrowRight" ? 1 : -1) + reviewStatusFilters.length) % reviewStatusFilters.length]);
        return;
      }
      if (event.key !== "ArrowUp" && event.key !== "ArrowDown" || !page?.items.length) return;
      event.preventDefault();
      const index = page.items.findIndex((task) => task.id === reviewId);
      const next = index < 0 ? (event.key === "ArrowDown" ? 0 : page.items.length - 1) : Math.min(page.items.length - 1, Math.max(0, index + (event.key === "ArrowDown" ? 1 : -1)));
      const task = page.items[next];
      select(task);
      const list = listRef.current;
      const card = list?.querySelector<HTMLElement>(`[data-review-id="${task.id}"]`);
      if (list && card && window.innerWidth > 900) {
        const bounds = list.getBoundingClientRect(), itemBounds = card.getBoundingClientRect();
        list.scrollTop = listScrollTopFor({ listTop: bounds.top, listHeight: list.clientHeight, scrollTop: list.scrollTop, optionTop: itemBounds.top, optionHeight: itemBounds.height, padding: 72 });
        card.focus({ preventScroll: true });
      }
    }
    window.addEventListener("keydown", shortcut);
    return () => window.removeEventListener("keydown", shortcut);
  });

  return <section className="code-review-library" aria-label="Code review tasks">
    <div className="status-filters" role="group" aria-label="Filter code reviews">
      {reviewStatusFilters.map((value) => <button className={`filter-button ${status === value ? "selected" : ""}`} type="button" key={value} aria-pressed={status === value} onClick={() => filter(value)}>{taskStatusLabels[value]}</button>)}
    </div>
    <section ref={split.surfaceRef} className={`work-surface code-review-surface ${split.resizing ? "is-resizing" : ""}`} style={split.surfaceStyle} aria-label="Code review surface">
      <div className="work-queue">
        <div className="work-queue-header"><span className="result-count" role="status">{page ? `${page.total} code review${page.total === 1 ? "" : "s"}` : "Loading code reviews…"}</span><span className="work-queue-sort">Sorted by last activity</span></div>
        <div className="work-queue-viewport">
          <div className={`work-queue-fade-top ${scrolled ? "is-visible" : ""}`} aria-hidden="true" />
          <div className="work-queue-list" ref={listRef} role="listbox" aria-label="Code reviews" onScroll={(event) => setScrolled(event.currentTarget.scrollTop > 4)}>
            {error && <div className="error-notice" role="alert"><p>{error}</p><button className="button button-secondary" onClick={retry}>Retry code reviews</button></div>}
            {page?.items.map((task) => <article key={task.id} className={`work-item-card queue-card ${reviewId === task.id ? "is-selected" : ""}`} role="option" tabIndex={0} aria-selected={reviewId === task.id} aria-label={task.title} data-review-id={task.id}
              onClick={() => select(task)} onKeyDown={(event) => { if (event.target === event.currentTarget && ["Enter", " "].includes(event.key)) { event.preventDefault(); select(task); } }}>
              <div className="queue-card-topline"><span className={`status-badge status-${task.status}`}><span />{taskStatusLabels[task.status]}</span><span className="queue-card-meta">Code review<span className="sep">·</span><time dateTime={task.updated_at}>{formatDateTime(task.updated_at)}</time></span></div>
              <h2 className="queue-card-title">{task.title}</h2><p className="queue-card-summary">{task.summary}</p>
              <div className="queue-card-footer"><span className="queue-card-arrow" aria-hidden="true">→</span><CardActions task={task} actions={actions} /></div>
            </article>)}
            {page?.total === 0 && <div className="queue-empty"><h2>No {status === "all" ? "" : `${taskStatusLabels[status].toLowerCase()} `}code reviews</h2><p>Reviews stay linked to their completed work items.</p></div>}
          </div>
          <div className="work-queue-fade-bottom" aria-hidden="true" />
        </div>
        {page && <TaskPagination page={page} offset={offset} onOffset={setOffset} busy={busy} />}
      </div>
      <div className="work-surface-resizer" role="separator" aria-orientation="vertical" aria-label="Resize the code review queue" aria-keyshortcuts="ArrowLeft ArrowRight Home End" aria-valuemin={WORK_SPLIT_MIN} aria-valuemax={WORK_SPLIT_MAX} aria-valuenow={Math.round(split.split)} aria-valuetext={`Queue ${Math.round(split.split)}% of the surface`} title="Drag to resize the queue. Double-click to reset." tabIndex={0} {...split.separatorProps}><span aria-hidden="true" /></div>
      <aside className={`work-detail-pane code-review-detail ${reviewId ? "is-open" : ""}`} role="region" aria-label="Code review detail">
        {reviewId ? <ReviewDetail key={`${projectId}:${workId}:${reviewId}`} projectId={projectId} workId={workId} reviewId={reviewId} refreshSignal={refreshSignal} onNavigate={onNavigate} onClose={() => select(null)} actions={actions} />
          : <div className="detail-empty"><span className="eyebrow">CODE REVIEW CONTEXT</span><h2>Pick a code review.</h2><p>Its scope, handoff, findings, and review history open here alongside the queue.</p><p className="detail-empty-hint">↑ ↓ select a review · ← → cycle states · C copy recall pointer</p></div>}
      </aside>
    </section>
  </section>;
}

function ReviewDetail({ projectId, workId, reviewId, refreshSignal, onNavigate, onClose, actions }: {
  projectId: string; workId: string | null; reviewId: string; refreshSignal: number;
  onNavigate: Navigate; onClose: () => void; actions: ReviewActions;
}) {
  const registry = useMutationIntentRegistry();
  const valid = validUuid(workId) && validUuid(reviewId);
  const { page: taskPage, error: taskError, retry: retryTask } = useTaskPage(projectId, refreshSignal, "all", "code_review", 0, reviewId, valid);
  const [detail, setDetail] = useState<CodeReviewDetail | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [retryCount, setRetryCount] = useState(0);
  const retry = () => { setRetryCount((value) => value + 1); retryTask(); };
  const closeRef = useRef<HTMLAnchorElement>(null);
  useEffect(() => {
    if (window.innerWidth > 900) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    closeRef.current?.focus();
    return () => { document.body.style.overflow = previous; };
  }, []);
  useEffect(() => {
    if (!validUuid(workId) || !validUuid(reviewId)) { setError("This code review link is invalid."); return; }
    const controller = new AbortController();
    setBusy(true); setError("");
    api<unknown>(`/projects/${projectId}/work-items/${workId}/code-reviews/${reviewId}`, { signal: controller.signal })
      .then((value) => { const next = decodeCodeReviewDetail(value, projectId, workId, reviewId); if (!controller.signal.aborted) setDetail(next); })
      .catch((error) => { if (!controller.signal.aborted) setError(errorMessage(error)); })
      .finally(() => { if (!controller.signal.aborted) setBusy(false); });
    return () => controller.abort();
  }, [projectId, workId, reviewId, refreshSignal, retryCount]);
  useFailedReadRetry({ scope: reviewId, failed: Boolean(error), busy, retry, enabled: valid });
  const parentPath = taskPath({ kind: "work_item", id: workId ?? "", work_item_id: workId ?? "", project_id: projectId });
  const task = taskPage?.items[0];
  return <div className="detail-scroll"><div className="review-detail-content">
    <Link ref={closeRef} className="text-link" href={`/code-reviews?project=${projectId}`} onClick={(event) => { event.preventDefault(); onClose(); }}>← Back to code reviews</Link>
    {(error || taskError) && <div className="error-notice" role="alert"><p>{error || taskError}</p><button className="button button-secondary" onClick={retry}>Retry review</button></div>}
    {!detail && !error && <div className="loading-state" role="status">Loading code review…</div>}
    {detail && <>
      <div className="task-detail-heading"><span className="task-kind">Code review</span><h2 className="detail-title">{detail.source_work_state.title}</h2><p>Child task of <Link href={parentPath} onClick={onNavigate}>{detail.source_work_state.title}</Link></p></div>
      {task && <div className="review-detail-actions"><CardActions task={task} actions={actions} /></div>}
      <ReviewResult detail={detail} statusLabel={task ? taskStatusLabels[task.status] : undefined} onOpen={(id) => { if (registry.hasDispatched()) return; window.location.assign(taskPath({ kind: "work_item", id, work_item_id: id, project_id: projectId })); }} />
      {!detail.source_work_state.deleted && <Link className="button button-secondary" href={`${parentPath}&review=1`} onClick={onNavigate}>Open review controls</Link>}
    </>}
  </div></div>;
}
