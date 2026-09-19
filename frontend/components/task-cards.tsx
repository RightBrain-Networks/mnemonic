"use client";

import Link from "next/link";
import type { MouseEvent } from "react";
import { formatDateTime } from "@/lib/display-time";
import { taskPath, taskStatusLabels, TASK_PAGE_SIZE, type Task, type TaskPage } from "@/lib/tasks";

export function TaskCard({ task, onNavigate }: { task: Task; onNavigate: (event: MouseEvent<HTMLAnchorElement>) => void }) {
  return <article className={`task-card task-card-${task.kind}`} aria-label={`${task.kind === "work_item" ? "Work item" : "Code review"}: ${task.title}`}>
    <div className="task-card-body">
      <div className="task-card-meta"><span className="task-kind">{task.kind === "work_item" ? "Work item" : "Code review"}</span><span className={`status-badge status-${task.status}`}><span />{taskStatusLabels[task.status]}</span></div>
      <h3>{task.title}</h3>
      <p>{task.summary}</p>
      <div className="task-card-footnote">{task.lease ? <span>{task.lease.holder_client} · {task.lease.holder_session_id}</span> : <span>Updated {formatDateTime(task.updated_at)}</span>}</div>
    </div>
    <Link className="button button-secondary task-view-link" href={taskPath(task)} onClick={onNavigate}>View<span aria-hidden="true">→</span></Link>
  </article>;
}

export function TaskPagination({ page, offset, onOffset, busy }: { page: TaskPage; offset: number; onOffset: (offset: number) => void; busy: boolean }) {
  if (page.total <= TASK_PAGE_SIZE && offset === 0) return null;
  return <nav className="task-pagination" aria-label="Task pages">
    <button className="button button-secondary" disabled={busy || offset === 0} onClick={() => onOffset(Math.max(0, offset - TASK_PAGE_SIZE))}>Previous</button>
    <span>{page.total ? `${Math.min(offset + 1, page.total)}–${Math.min(offset + TASK_PAGE_SIZE, page.total)} of ${page.total}` : "0 tasks"}</span>
    <button className="button button-secondary" disabled={busy || offset + TASK_PAGE_SIZE >= page.total} onClick={() => onOffset(offset + TASK_PAGE_SIZE)}>Next</button>
  </nav>;
}
