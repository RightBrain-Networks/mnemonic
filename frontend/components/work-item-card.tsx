import { formatDate, formatDateTime } from "@/lib/display-time";
import type { LeasePublic, Readiness, WorkStatus } from "@/lib/types";

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
  if (status !== "pending" || !readiness || readiness.is_duplicate) return status;
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
    {readiness.is_duplicate && <span className="operational-badge duplicate">Duplicate</span>}
    {readiness.is_gated && readiness.display_state !== "waiting" && <span className="operational-badge waiting">Needs attention</span>}
    {readiness.is_blocked && readiness.display_state !== "blocked" && <span className="operational-badge blocked">Blocked</span>}
    {readiness.has_active_lease && readiness.display_state !== "active" && <span className="operational-badge active">{readiness.active_lease?.purpose === "code_review" ? "Review in progress" : "Active"}</span>}
  </>;
}

function ActiveLeaseSummary({ lease, detailed = false }: { lease: LeasePublic; detailed?: boolean }) {
  return <section className={`active-lease-summary ${detailed ? "active-lease-detail" : ""}`} aria-label={lease.purpose === "code_review" ? "Active review lease" : "Active work lease"}>
    <div className="active-lease-holder">
      <span className="lease-label">{lease.purpose === "code_review" ? `${lease.mode === "cold" ? "Cold" : "Warm"} review session` : "Active session"}</span>
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

export {
  ActiveLeaseSummary,
  OperationalBadge,
  StatusBadge,
  clientLabel,
  effectiveCardStatus,
  formatDate,
  formatDateTime,
  statusLabels
};
