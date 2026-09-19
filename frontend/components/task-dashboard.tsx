"use client";

import Link from "next/link";
import { useState, type MouseEvent } from "react";
import { useTaskPage } from "@/components/use-task-page";
import { TaskCard, TaskPagination } from "@/components/task-cards";

export default function TaskDashboard({ projectId, refreshSignal, onNavigate }: {
  projectId: string; refreshSignal: number; onNavigate: (event: MouseEvent<HTMLAnchorElement>) => void;
}) {
  const [offset, setOffset] = useState(0);
  const { page, error, busy, retry } = useTaskPage(projectId, refreshSignal, "active", undefined, offset);
  return <div className="task-dashboard">
    <section className="task-widgets" aria-label="Task overview">
      {([
        ["Work items", "work_items", "/work-items", "Work assigned to an agent session."],
        ["Code reviews", "code_reviews", "/code-reviews", "Adversarial reviews of completed work."]
      ] as const).map(([label, key, path, description]) => <article className={`task-widget task-widget-${key}`} key={key} aria-label={label}>
        <div className="task-widget-heading"><h2>{label}</h2><Link href={path} onClick={onNavigate} aria-label={`View ${label.toLowerCase()}`}><span aria-hidden="true">↗</span></Link></div>
        <p>{description}</p>
        <dl className="task-counts">
          <div><dt><span className="task-active-dot" />Active</dt><dd>{page?.[key].active ?? "—"}</dd></div>
          <div><dt>Pending</dt><dd>{page?.[key].pending ?? "—"}</dd></div>
        </dl>
      </article>)}
    </section>
    <section className="active-tasks" aria-labelledby="active-tasks-heading">
      <div className="task-section-heading"><h2 id="active-tasks-heading">Active tasks</h2>{page && <span>{page.total} in progress</span>}</div>
      <p className="task-section-description">Work items and code reviews currently assigned to an agent session.</p>
      {error && <div className="error-notice" role="alert"><p>{error}</p><button className="button button-secondary" onClick={retry}>Retry tasks</button></div>}
      {!page && !error && <div className="loading-state" role="status">Loading tasks…</div>}
      {page && <>
        {page.total === 0 ? <div className="task-empty-state"><h3>No active tasks</h3><p>Tasks appear here when an agent starts a work item or code review.</p></div> : <div className="task-card-list">{page.items.map((task) => <TaskCard key={task.id} task={task} onNavigate={onNavigate} />)}</div>}
        <TaskPagination page={page} offset={offset} onOffset={setOffset} busy={busy} />
      </>}
    </section>
  </div>;
}
