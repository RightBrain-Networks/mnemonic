"use client";

import Link from "next/link";
import { useEffect, useState, type MouseEvent } from "react";
import { api, errorMessage } from "@/lib/api";
import { decodeCodeReviewDetail, type CodeReviewDetail } from "@/lib/code-reviews";
import { taskPath, taskStatusLabels, type TaskStatus } from "@/lib/tasks";
import { validUuid } from "@/lib/wire-guards";
import { TaskCard, TaskPagination } from "@/components/task-cards";
import { useTaskPage } from "@/components/use-task-page";
import { useFailedReadRetry } from "@/components/use-failed-read-retry";
import { ReviewResult } from "@/components/code-review-panel";

export default function CodeReviewLibrary({ projectId, refreshSignal, workId, reviewId, onNavigate }: {
  projectId: string; refreshSignal: number; workId: string | null; reviewId: string | null;
  onNavigate: (event: MouseEvent<HTMLAnchorElement>) => void;
}) {
  const [status, setStatus] = useState<TaskStatus | "all">("pending");
  const [offset, setOffset] = useState(0);
  const { page, error, busy, retry } = useTaskPage(projectId, refreshSignal, status, "code_review", offset, undefined, !reviewId);
  if (reviewId) return <ReviewDetail key={`${projectId}:${workId}:${reviewId}`} projectId={projectId} workId={workId} reviewId={reviewId} refreshSignal={refreshSignal} onNavigate={onNavigate} />;
  return <section className="code-review-library" aria-label="Code review tasks">
    <div className="status-filters" role="group" aria-label="Filter code reviews">
      {(["pending", "active", "deferred", "done", "wont-do", "promoted", "superseded", "all"] as const).map((value) => <button className={`filter-button ${status === value ? "selected" : ""}`} type="button" key={value} aria-pressed={status === value} onClick={() => { setStatus(value); setOffset(0); }}>{taskStatusLabels[value]}</button>)}
    </div>
    {error && <div className="error-notice" role="alert"><p>{error}</p><button className="button button-secondary" onClick={retry}>Retry code reviews</button></div>}
    {!page && !error && <div className="loading-state" role="status">Loading code reviews…</div>}
    {page && <>
      <p className="task-result-count">{page.total} code review{page.total === 1 ? "" : "s"}</p>
      {page.total === 0 ? <div className="task-empty-state"><h2>No {status === "all" ? "" : `${taskStatusLabels[status].toLowerCase()} `}code reviews</h2><p>Reviews are created for completed work items. Each review stays linked to its original work item.</p></div> : <div className="task-card-list">{page.items.map((task) => <TaskCard key={task.id} task={task} onNavigate={onNavigate} />)}</div>}
      <TaskPagination page={page} offset={offset} onOffset={setOffset} busy={busy} />
    </>}
  </section>;
}

function ReviewDetail({ projectId, workId, reviewId, refreshSignal, onNavigate }: {
  projectId: string; workId: string | null; reviewId: string; refreshSignal: number;
  onNavigate: (event: MouseEvent<HTMLAnchorElement>) => void;
}) {
  const { page: taskPage } = useTaskPage(projectId, refreshSignal, "all", "code_review", 0, reviewId, validUuid(workId) && validUuid(reviewId));
  const [detail, setDetail] = useState<CodeReviewDetail | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [retryCount, setRetryCount] = useState(0);
  const retry = () => setRetryCount((value) => value + 1);
  useEffect(() => {
    if (!validUuid(workId) || !validUuid(reviewId)) { setError("This code review link is invalid."); return; }
    const controller = new AbortController();
    setBusy(true);
    setError("");
    api<unknown>(`/projects/${projectId}/work-items/${workId}/code-reviews/${reviewId}`, { signal: controller.signal })
      .then((value) => { const next = decodeCodeReviewDetail(value, projectId, workId, reviewId); if (!controller.signal.aborted) setDetail(next); })
      .catch((error) => { if (!controller.signal.aborted) setError(errorMessage(error)); })
      .finally(() => { if (!controller.signal.aborted) setBusy(false); });
    return () => controller.abort();
  }, [projectId, workId, reviewId, refreshSignal, retryCount]);
  useFailedReadRetry({ scope: reviewId, failed: Boolean(error), busy, retry, enabled: validUuid(workId) && validUuid(reviewId) });
  const parentPath = taskPath({ kind: "work_item", id: workId ?? "", work_item_id: workId ?? "", project_id: projectId });
  return <section className="code-review-detail" aria-label="Code review detail">
    <Link className="text-link" href="/code-reviews" onClick={onNavigate}>← Back to code reviews</Link>
    {error && <div className="error-notice" role="alert"><p>{error}</p><button className="button button-secondary" onClick={retry}>Retry review</button></div>}
    {!detail && !error && <div className="loading-state" role="status">Loading code review…</div>}
    {detail && <>
      <div className="task-detail-heading"><span className="task-kind">Code review</span><h2>{detail.source_work_state.title}</h2><p>Child task of <Link href={parentPath} onClick={onNavigate}>{detail.source_work_state.title}</Link></p></div>
      <ReviewResult detail={detail} statusLabel={taskPage?.items[0] ? taskStatusLabels[taskPage.items[0].status] : undefined} onOpen={(id) => { window.location.assign(taskPath({ kind: "work_item", id, work_item_id: id, project_id: projectId })); }} />
      {!detail.source_work_state.deleted && <Link className="button button-secondary" href={`${parentPath}&review=1`} onClick={onNavigate}>Open review controls</Link>}
    </>}
  </section>;
}
