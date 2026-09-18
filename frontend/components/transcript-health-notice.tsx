"use client";

import { useEffect, useState } from "react";
import { formatArtifactSize } from "@/lib/artifacts";
import { decodeTranscriptHealth, readDismissedTranscriptWarnings, transcriptWarningKey, type TranscriptDirectoryUsage, type TranscriptHealth, type TranscriptWarning } from "@/lib/transcript-health";
import { transcriptPath, transcriptRequest } from "@/lib/transcripts";

function Warning({ warning, onDismiss }: { warning: TranscriptWarning; onDismiss: () => void }) {
  return <li>
    <div className="transcript-warning-heading"><strong>{warning.message}{warning.affected > 0 && ` · ${warning.affected} transcript${warning.affected === 1 ? "" : "s"}`}</strong><button className="text-button" type="button" onClick={onDismiss} aria-label={`Dismiss ${warning.message.toLowerCase()} notification`}>Dismiss</button></div>
    {warning.path && <code className="transcript-health-path">{warning.path}</code>}
    <p>{warning.action}</p>
    {warning.owner_uid !== null && <p className="transcript-health-metadata">Observed by {warning.service}: owner {warning.owner_uid}:{warning.owner_gid}, mode {warning.mode}; service {warning.uid}:{warning.gid}.</p>}
    {warning.retry_at && <p className="transcript-health-metadata">Mnemonic rechecks automatically while work is inactive and indexing is enabled.</p>}
  </li>;
}
function StorageItem({ label, usage }: { label: string; usage: TranscriptDirectoryUsage | null }) {
  const stale = usage && Date.now() - Date.parse(usage.checked_at) > 120000;
  return <div><dt>{label}</dt><dd>{usage?.bytes != null ? `${usage.complete ? "" : "At least "}${formatArtifactSize(usage.bytes)}` : "Unavailable"}</dd>
    <small>{usage?.free_bytes != null ? `${formatArtifactSize(usage.free_bytes)} free on volume` : "Volume space unavailable"}{stale ? " · Last reported" : ""}</small>
    {usage && !usage.complete && <small>Storage measurement incomplete</small>}
  </div>;
}

export default function TranscriptHealthNotice({ projectId, refreshSignal }: { projectId: string; refreshSignal: number }) {
  const [health, setHealth] = useState<TranscriptHealth | null>(null);
  const [failed, setFailed] = useState(false);
  const [dismissed, setDismissed] = useState<string[]>([]);
  const storageKey = `mnemonic:transcript-warnings:${projectId}`;
  useEffect(() => {
    try { setDismissed(readDismissedTranscriptWarnings(localStorage.getItem(storageKey))); } catch { setDismissed([]); }
  }, [storageKey]);
  useEffect(() => {
    const controller = new AbortController();
    setFailed(false);
    void transcriptRequest(`${transcriptPath(projectId)}/health`, { signal: controller.signal })
      .then((value) => { if (!controller.signal.aborted) { setHealth(decodeTranscriptHealth(value, projectId)); setFailed(false); } })
      .catch(() => { if (!controller.signal.aborted) setFailed(true); });
    return () => controller.abort();
  }, [projectId, refreshSignal]);
  function saveDismissed(values: string[]) {
    const next = [...new Set(values)].slice(-200);
    setDismissed(next);
    try { localStorage.setItem(storageKey, JSON.stringify(next)); } catch { /* Session dismissal still works. */ }
  }
  const warnings = health?.warnings.filter((warning) => !dismissed.includes(transcriptWarningKey(warning))) ?? [];
  const omittedKey = `omitted:${health?.warnings_omitted ?? 0}`;
  const omitted = health?.warnings_omitted && !dismissed.includes(omittedKey) ? health.warnings_omitted : 0;
  const hidden = (health?.warnings.length ?? 0) - warnings.length + (health?.warnings_omitted && !omitted ? health.warnings_omitted : 0);
  const renderWarning = (warning: TranscriptWarning) => <Warning key={transcriptWarningKey(warning)} warning={warning} onDismiss={() => saveDismissed([...dismissed, transcriptWarningKey(warning)])} />;
  return <>
    {health?.storage && <section className="transcript-storage" aria-label="Transcript storage usage"><div className="transcript-storage-heading"><strong>Storage</strong><span>All projects · disk space used</span></div><dl>
      <StorageItem label="Retained transcripts" usage={health.storage.transcripts} />
      <StorageItem label="Search index" usage={health.storage.index} />
      <div><dt>Conversation database</dt><dd>{formatArtifactSize(health.storage.database_bytes)}</dd><small>Text, metadata and database indexes</small></div>
    </dl></section>}
    {failed && <aside className="transcript-health-notice" role="status"><strong>Transcript health could not be checked.</strong><p>Refresh to retry.</p></aside>}
    {Boolean(warnings.length || omitted) && <aside className="transcript-health-notice" role="status" aria-label="Transcript access warnings">
      <h2>Transcript access needs attention</h2>
      <ul>{warnings.slice(0, 3).map(renderWarning)}</ul>
      {warnings.length > 3 && <details><summary>Show {warnings.length - 3} more warnings</summary><ul>{warnings.slice(3).map(renderWarning)}</ul></details>}
      {Boolean(omitted) && <p>{omitted} additional problems are not shown. <button type="button" className="text-button" onClick={() => saveDismissed([...dismissed, omittedKey])}>Dismiss additional warning notice</button></p>}
      <p className="transcript-health-metadata">Dismissing hides this notification in this browser. Failure details remain in the transcript table.</p>
    </aside>}
    {hidden > 0 && <p className="artifact-filter-note">{hidden} notification{hidden === 1 ? "" : "s"} dismissed. <button className="text-button" type="button" onClick={() => saveDismissed([])}>Show dismissed notifications</button></p>}
  </>;
}
