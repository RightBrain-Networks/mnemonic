import Link from "@/components/dashboard-link";
import { knowledgeLibraryPath } from "@/lib/knowledge";
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
    <section className="job-decision-provenance" aria-label="Decision provenance">
      <strong>Decision provenance</strong>
      {!report.decision_provenance ? <p>Decision provenance not recorded.</p> : <>
        <p>{report.decision_provenance.disposition === "cited" ? "Registered decision citations" : report.decision_provenance.disposition === "not-code" ? "Not code" : "No applicable decisions"}</p>
        {report.decision_provenance.reason && <p>{report.decision_provenance.reason}</p>}
        {!!report.decision_provenance.citations.length && <ul>{report.decision_provenance.citations.map((entry) => <li key={`${entry.decision_id}-${entry.revision}`}><Link href={knowledgeLibraryPath(report.project_id, entry.decision_id, entry.revision)}>{entry.decision_id}-{entry.revision}</Link></li>)}</ul>}
        {!!report.decision_provenance.commits.length && <><ul>{report.decision_provenance.commits.map((entry) => <li key={`${entry.repository}-${entry.commit_hash}`}><span className="break-all">{entry.repository}</span><br /><code className="break-all">{entry.commit_hash}</code></li>)}</ul><p className="field-hint">Author-declared commits; Git history is not verified by Mnemonic.</p></>}
      </>}
    </section>
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
