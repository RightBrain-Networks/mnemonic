import type { JobReportEnvelope } from "@/lib/types";
import { formatDateTime, statusLabels } from "@/components/work-item-card";
import MarkdownContent from "@/components/markdown-content";

export default function JobReportContent({ item }: { item: JobReportEnvelope }) {
  const { report, source_work_state: source } = item;
  const changed = source.status !== report.closeout_status;
  return <div className="job-report-content">
    <div className="report-heading"><span className={`status-badge status-${report.closeout_status}`}>{statusLabels[report.closeout_status]}</span><time dateTime={report.created_at}>{formatDateTime(report.created_at)}</time></div>
    <h3 dir="auto">{report.work_title_at_closeout}</h3>
    <MarkdownContent className="human-report-summary">{report.summary}</MarkdownContent>
    {report.fyi_items.length > 0 && <ul className="human-report-fyis">{report.fyi_items.map((item, index) => <li key={index} dir="auto"><MarkdownContent>{item}</MarkdownContent></li>)}</ul>}
    {(changed || source.deleted || source.canonical_work_item_id !== source.work_item_id) && <p className="report-source-state">
      This report records the earlier {statusLabels[report.closeout_status]} closeout.
      {source.deleted ? " The original work is now deleted." : changed ? ` The work is now ${statusLabels[source.status]}.` : ""}
      {source.canonical_work_item_id !== source.work_item_id ? " The original work has since been merged; these records still identify its exact source." : ""}
    </p>}
    <details className="report-provenance"><summary>Report details</summary><dl>
      <div><dt>Report</dt><dd className="mono break-all">{report.id}</dd></div>
      <div><dt>Original work</dt><dd className="mono break-all">{report.work_item_id}</dd></div>
      <div><dt>Closeout version</dt><dd>{report.closeout_work_version}</dd></div>
      <div><dt>Author</dt><dd dir="auto">{report.actor_client} · {report.actor_session_id}{report.actor_model ? ` · ${report.actor_model}` : ""}</dd></div>
    </dl></details>
  </div>;
}
