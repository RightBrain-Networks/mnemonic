import type { ReactNode } from "react";
import type { LiveSyncStatus } from "@/lib/live-sync";

const liveSyncLabels: Record<LiveSyncStatus, string> = {
  live: "Live updates",
  retrying: "Reconnecting…",
  connecting: "Connecting…"
};

export default function DashboardViewChrome({
  eyebrow,
  title,
  subject,
  description,
  liveSyncStatus,
  onRefresh,
  actions
}: {
  eyebrow: string;
  title: string;
  subject?: string;
  description: string;
  liveSyncStatus: LiveSyncStatus;
  onRefresh: () => void;
  actions?: ReactNode;
}) {
  return <section className="page-heading">
    <div>
      <div className="eyebrow">{eyebrow}</div>
      <h1>{title}<span className="heading-mark">{subject ? ":" : "."}</span>{subject && <>{" "}<span className="heading-subject">{subject}</span></>}</h1>
      <p>{description}</p>
    </div>
    <div className="heading-actions">
      <button className="button button-secondary" type="button" onClick={onRefresh}>Refresh</button>
      {actions}
      <div
        className={`sync-status sync-status-${liveSyncStatus}`}
        role="status"
        aria-live="polite"
      ><span className="sync-status-dot" />{liveSyncLabels[liveSyncStatus]}</div>
    </div>
  </section>;
}
