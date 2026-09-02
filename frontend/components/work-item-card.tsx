import { formatDate, formatDateTime } from "@/lib/display-time";
import type { LeasePublic, Readiness, WorkSummary, WorkStatus } from "@/lib/types";

const statusLabels: Record<WorkStatus, string> = {
  pending: "Pending",
  deferred: "Deferred",
  done: "Done",
  "wont-do": "Won’t do",
  promoted: "Promoted"
};

function clientLabel(client: string) {
  return ({
    "claude-code": "Claude Code",
    chatgpt: "ChatGPT",
    opencode: "OpenCode",
    dashboard: "Dashboard",
    manual: "Manual capture"
  } as Record<string, string>)[client] ?? client;
}

type CardStatus = WorkStatus | "active" | "dropped" | "blocked" | "waiting";

const cardStatusLabels: Record<CardStatus, string> = {
  ...statusLabels,
  active: "Active",
  dropped: "Dropped",
  blocked: "Blocked",
  waiting: "Needs attention"
};

function effectiveCardStatus(status: WorkStatus, readiness?: Readiness): CardStatus {
  if (status !== "pending" || !readiness) return status;
  if (readiness.is_gated) return "waiting";
  if (readiness.is_blocked) return "blocked";
  if (readiness.has_active_lease) return "active";
  if (readiness.has_dropped_lease) return "dropped";
  return "pending";
}

function StatusBadge({ status, readiness }: { status: WorkStatus; readiness?: Readiness }) {
  const effective = effectiveCardStatus(status, readiness);
  return <span className={`status-badge status-${effective}`}><span />{cardStatusLabels[effective]}</span>;
}

function OperationalBadge({ readiness }: { readiness: Readiness }) {
  return <>
    {readiness.is_gated && readiness.display_state !== "waiting" && <span className="operational-badge waiting">Needs attention</span>}
    {readiness.is_blocked && readiness.display_state !== "blocked" && <span className="operational-badge blocked">Blocked</span>}
    {readiness.has_active_lease && readiness.display_state !== "active" && <span className="operational-badge active">Active</span>}
  </>;
}

function ActiveLeaseSummary({ lease, detailed = false }: { lease: LeasePublic; detailed?: boolean }) {
  return <section className={`active-lease-summary ${detailed ? "active-lease-detail" : ""}`} aria-label="Active work lease">
    <div className="active-lease-holder">
      <span className="lease-label">Active session</span>
      <strong>{clientLabel(lease.holder_client)}</strong>
      <span className="mono" title={lease.holder_session_id}>{lease.holder_session_id}</span>
    </div>
    <dl className="active-lease-times">
      <div><dt>Lease acquired</dt><dd><time dateTime={lease.acquired_at}>{formatDateTime(lease.acquired_at)}</time></dd></div>
      <div><dt>Renewed</dt><dd><time dateTime={lease.renewed_at}>{formatDateTime(lease.renewed_at)}</time></dd></div>
      <div><dt>Expires</dt><dd><time dateTime={lease.expires_at}>{formatDateTime(lease.expires_at)}</time></dd></div>
    </dl>
    {detailed && <p className="active-lease-note">This lease records a temporary active session. Its capability never enters the dashboard.</p>}
  </section>;
}

type Props = {
  summary: WorkSummary;
  copied: boolean;
  onOpen: () => void;
  onEdit: () => void;
  onDelete: () => void;
  onDefer: () => void;
  onCopyPointer: () => void;
  deferring: boolean;
};

export default function WorkItemCard({
  summary,
  copied,
  onOpen,
  onEdit,
  onDelete,
  onDefer,
  onCopyPointer,
  deferring
}: Props) {
  const work = summary.work_item;
  const context = summary.current_context;
  return <article className="work-item-card">
    <div className="card-topline">
      <StatusBadge status={work.status} readiness={summary.readiness} />
      <OperationalBadge readiness={summary.readiness} />
      <span className="card-source">
        Current context · {clientLabel(context.source_client)}
        <span>·</span>
        Last activity
        <time dateTime={work.updated_at} title={formatDateTime(work.updated_at)}>
          {formatDateTime(work.updated_at)}
        </time>
      </span>
      <span className="card-version">v{work.version}</span>
    </div>
    <button className="card-title" type="button" onClick={onOpen}>
      <h2>{work.title}</h2><span aria-hidden="true">→</span>
    </button>
    <p className="card-summary">{work.summary}</p>
    <div className="work-card-facts" aria-label="Work item facts">
      <span>{summary.checkpoint_count} checkpoint{summary.checkpoint_count === 1 ? "" : "s"}</span>
      <span>Priority {work.priority}</span>
      <span className="mono" title={context.source_session_id}>session {context.source_session_id}</span>
    </div>
    {summary.readiness.active_lease && <ActiveLeaseSummary lease={summary.readiness.active_lease} />}
    <div className="card-footer">
      <div className="card-context">
        {context.tags.slice(0, 3).map((tag) => <span className="tag" key={tag}>{tag}</span>)}
        {context.tags.length > 3 && <span className="extra-tags">+{context.tags.length - 3}</span>}
        {context.migration_origin === "legacy-handoff-snapshot" &&
          <span className="migration-chip">Migrated snapshot</span>}
      </div>
      <div className="card-actions">
        {(work.status === "pending" || work.status === "deferred") && <button
          className="button defer-button"
          type="button"
          disabled={deferring || summary.readiness.has_active_lease}
          aria-label={work.status === "deferred" ? `Move ${work.title} to Pending` : `Defer ${work.title}`}
          title={summary.readiness.has_active_lease
            ? "Active work cannot be deferred until its lease is released or expires."
            : work.status === "deferred"
              ? "Move this work item back to Pending; blockers and human gates still apply"
              : "Hold this work item out of the work queue"}
          onClick={onDefer}
        >{deferring ? "Saving…" : work.status === "deferred" ? "Move to Pending" : "Defer"}</button>}
        <button className="icon-button" type="button" aria-label={`Edit ${work.title}`} title="Edit work item" onClick={onEdit}>✎</button>
        <button
          className="icon-button danger-hover"
          type="button"
          aria-label={`Delete ${work.title}`}
          title={summary.readiness.is_gated
            ? "Resolve every human question before deleting this work item."
            : "Delete work item"}
          disabled={summary.readiness.is_gated}
          onClick={onDelete}
        >⌫</button>
        <span className="action-divider" />
        <button className={`button copy-button ${copied ? "is-copied" : ""}`} type="button" onClick={onCopyPointer}>
          {copied ? "Copied" : "Copy recall pointer"}
        </button>
      </div>
    </div>
  </article>;
}

export { ActiveLeaseSummary, OperationalBadge, StatusBadge, clientLabel, formatDate, formatDateTime, statusLabels };
