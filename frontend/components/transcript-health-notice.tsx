"use client";

import { useEffect, useState } from "react";
import { decodeTranscriptHealth, type TranscriptHealth, type TranscriptWarning } from "@/lib/transcript-health";
import { transcriptPath, transcriptRequest } from "@/lib/transcripts";

function Warning({ warning }: { warning: TranscriptWarning }) {
  return <li>
    <strong>{warning.message}</strong>{warning.affected > 0 && <span> · {warning.affected} transcript{warning.affected === 1 ? "" : "s"}</span>}
    {warning.path && <code className="transcript-health-path">{warning.path}</code>}
    <p>{warning.action}</p>
    {warning.owner_uid !== null && <p className="transcript-health-metadata">Observed by {warning.service}: owner {warning.owner_uid}:{warning.owner_gid}, mode {warning.mode}; service {warning.uid}:{warning.gid}.</p>}
    {warning.retry_at && <p className="transcript-health-metadata">Mnemonic will recheck access automatically. Work that is Active or paused continues to wait.</p>}
  </li>;
}

export default function TranscriptHealthNotice({ projectId, refreshSignal }: { projectId: string; refreshSignal: number }) {
  const [health, setHealth] = useState<TranscriptHealth | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    setFailed(false);
    void transcriptRequest(`${transcriptPath(projectId)}/health`, { signal: controller.signal })
      .then((value) => { if (!controller.signal.aborted) { setHealth(decodeTranscriptHealth(value, projectId)); setFailed(false); } })
      .catch(() => { if (!controller.signal.aborted) setFailed(true); });
    return () => controller.abort();
  }, [projectId, refreshSignal]);
  if (failed) return <aside className="transcript-health-notice" role="status"><strong>Transcript health could not be checked.</strong><p>Refresh to retry. Copy and permission problems may still be present.</p></aside>;
  if (!health?.warnings.length && !health?.warnings_omitted) return null;
  return <aside className="transcript-health-notice" role="status" aria-label="Transcript access warnings">
    <h2>Transcript access needs attention</h2>
    <p>{health.affected_transcripts > 0 ? `${health.affected_transcripts} transcript${health.affected_transcripts === 1 ? " needs" : "s need"} source or storage attention. ` : ""}Check the paths and service settings below.</p>
    <ul>{health.warnings.slice(0, 3).map((warning, index) => <Warning key={index} warning={warning} />)}</ul>
    {health.warnings.length > 3 && <details><summary>Show {health.warnings.length - 3} more warnings</summary><ul>{health.warnings.slice(3).map((warning, index) => <Warning key={index} warning={warning} />)}</ul></details>}
    {health.warnings_omitted > 0 && <p>{health.warnings_omitted} additional problems are not shown. Resolve these issues and refresh to see the remaining paths.</p>}
    <p className="transcript-health-metadata">Worker checked: {health.worker_checked_at ? new Date(health.worker_checked_at).toLocaleString() : "No report received"}. Environmental failures are rechecked every {Math.ceil(health.recheck_seconds / 60)} minutes.</p>
  </aside>;
}
