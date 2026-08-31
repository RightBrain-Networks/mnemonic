import type { LeasePublic, Readiness, WorkSummary, WorkStatus } from "@/lib/types";

const statusLabels: Record<WorkStatus, string> = {
  open: "Open",
  done: "Done",
  "wont-do": "Won’t do",
  promoted: "Promoted"
};

function formatDate(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", year: "numeric" }).format(date);
}

function formatDateTime(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short"
  }).format(date);
}

function clientLabel(client: string) {
  return ({
    "claude-code": "Claude Code",
    chatgpt: "ChatGPT",
    opencode: "OpenCode",
    dashboard: "Dashboard",
    manual: "Manual capture"
  } as Record<string, string>)[client] ?? client;
}

function StatusBadge({ status }: { status: WorkStatus }) {
  return <span className={`status-badge status-${status}`}><span />{statusLabels[status]}</span>;
}

function OperationalBadge({ readiness }: { readiness: Readiness }) {
  if (readiness.has_active_lease) {
    return <span className="operational-badge active">Active</span>;
  }
  if (readiness.is_ready) {
    return <span className="operational-badge ready">Ready</span>;
  }
  return null;
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
  onCopyPointer: () => void;
};

export default function WorkItemCard({
  summary,
  copied,
  onOpen,
  onEdit,
  onDelete,
  onCopyPointer
}: Props) {
  const work = summary.work_item;
  const context = summary.current_context;
  return <article className="handoff-card work-item-card">
    <div className="card-topline">
      <StatusBadge status={work.status} />
      <OperationalBadge readiness={summary.readiness} />
      <span className="card-source">
        Current context · {clientLabel(context.source_client)}
        <span>·</span>
        Last activity
        <time dateTime={work.updated_at} title={new Date(work.updated_at).toLocaleString()}>
          {formatDate(work.updated_at)}
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
        <button className="icon-button" type="button" aria-label={`Edit ${work.title}`} title="Edit work item" onClick={onEdit}>✎</button>
        <button className="icon-button danger-hover" type="button" aria-label={`Delete ${work.title}`} title="Delete work item" onClick={onDelete}>⌫</button>
        <span className="action-divider" />
        <button className={`button copy-button ${copied ? "is-copied" : ""}`} type="button" onClick={onCopyPointer}>
          {copied ? "Copied" : "Copy recall pointer"}
        </button>
      </div>
    </div>
  </article>;
}

export { ActiveLeaseSummary, OperationalBadge, StatusBadge, clientLabel, formatDate, formatDateTime, statusLabels };
