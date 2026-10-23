"use client";

import { useEffect, useId, useRef, useState } from "react";
import { errorMessage } from "@/lib/api";
import { dashboardSessionId } from "@/lib/dashboard-session";
import { dashboardMutationActor } from "@/lib/work-events";
import { pendingJobReportIds } from "@/lib/pending-job-reports";
import {
  mutationReportKey, useMutationIntentRegistry, useMutationScope,
  type MutationIntentSummary
} from "@/lib/mutation-intent";
import MutationRecoveryPanel from "@/components/mutation-recovery-panel";

type Props = {
  projectId: string;
  projectName: string;
  disabled: boolean;
  onCleared: (reportIds: string[]) => void;
  onSettled: () => void;
  onComplete: (count: number) => void;
};

function ClearSummariesDialog({ projectId, projectName, onCleared, onSettled, onComplete, onClose }: Omit<Props, "disabled"> & { onClose: () => void }) {
  const registry = useMutationIntentRegistry();
  const { blocked, intents } = useMutationScope({ projectId, kinds: ["dismiss_job_completion_report", "create_job_completion_report_follow_up"] });
  const [reportIds, setReportIds] = useState<string[] | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [started, setStarted] = useState(false);
  const [clearedCount, setClearedCount] = useState(0);
  const [retrying, setRetrying] = useState("");
  const cleared = useRef(new Set<string>());
  const performing = useRef(false);
  const alive = useRef(true);
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const descriptionId = useId();
  const slot = (id: string) => `dismiss-report:${projectId}:${id}`;

  useEffect(() => {
    alive.current = true;
    const controller = new AbortController();
    const element = dialog.current;
    element?.showModal();
    void pendingJobReportIds(projectId, controller.signal).then((ids) => {
      if (!controller.signal.aborted) setReportIds(ids);
    }).catch((failure) => {
      if (!controller.signal.aborted) setError(errorMessage(failure));
    });
    return () => { alive.current = false; controller.abort(); element?.close(); };
  }, [projectId]);

  function close() {
    if (!performing.current) { dialog.current?.close(); onClose(); }
  }
  function recordCleared(id: string) {
    if (cleared.current.has(id)) return;
    cleared.current.add(id);
    setClearedCount(cleared.current.size);
    onCleared([id]);
  }
  function finishIfComplete() {
    if (reportIds && cleared.current.size === reportIds.length) {
      dialog.current?.close();
      onClose();
      onComplete(cleared.current.size);
    }
  }
  async function clearAll() {
    if (!reportIds?.length || performing.current || blocked) return;
    performing.current = true; setBusy(true); setStarted(true); setError("");
    const actor = dashboardMutationActor(dashboardSessionId());
    try {
      for (const id of reportIds) {
        // Project switching unmounts this dialog. Never dispatch the rest afterward.
        if (!alive.current) return;
        if (cleared.current.has(id)) continue;
        await registry.execute({
          kind: "dismiss_job_completion_report", slot: slot(id), projectId,
          conflictKeys: [mutationReportKey(projectId, id)], method: "POST",
          path: `/projects/${projectId}/job-completion-reports/${id}/dismiss`, payload: { actor }
        });
        if (!alive.current) return;
        recordCleared(id);
      }
      finishIfComplete();
    } catch (failure) { if (alive.current) setError(errorMessage(failure)); }
    finally {
      performing.current = false;
      if (alive.current) { setBusy(false); onSettled(); }
    }
  }
  async function retry(intent: MutationIntentSummary) {
    const id = reportIds?.find((candidate) => slot(candidate) === intent.slot);
    if (!id || performing.current) return;
    performing.current = true; setRetrying(intent.slot);
    try {
      await registry.retry(intent.slot);
      if (!alive.current) return;
      recordCleared(id); setError(""); finishIfComplete();
    } catch (failure) { if (alive.current) setError(errorMessage(failure)); }
    finally {
      performing.current = false;
      if (alive.current) { setRetrying(""); onSettled(); }
    }
  }
  const working = busy || Boolean(retrying);
  return <dialog ref={dialog} className="dialog" aria-labelledby={titleId} aria-describedby={descriptionId}
    onCancel={(event) => { event.preventDefault(); close(); }}>
    <div className="dialog-header"><h2 id={titleId}>Clear all summaries?</h2></div>
    <div className="dialog-content">
      <p id={descriptionId} className="dialog-intro">{reportIds === null
        ? `Find all pending summaries in “${projectName}” before confirming.`
        : `Dismiss ${reportIds.length} pending ${reportIds.length === 1 ? "summary" : "summaries"} in “${projectName}”?`}
        {" "}Stored reports and work history will remain available.</p>
      {reportIds === null && !error && <p role="status"><span className="spinner" />Loading pending summaries…</p>}
      {reportIds !== null && <p className="field-hint">Only the summaries counted here will be dismissed. Later summaries will stay pending.</p>}
      {reportIds?.length === 0 && <p role="status">There are no pending summaries to clear.</p>}
      {started && <p role="status" aria-live="polite">Cleared {clearedCount} of {reportIds?.length} summaries.</p>}
      {error && <p className="error-notice" role="alert">{error}</p>}
      <MutationRecoveryPanel intents={busy ? [] : intents} retryingMutation={retrying} modal onRetry={(intent) => void retry(intent)} />
      <div className="dialog-actions">
        <button type="button" className="button button-secondary" autoFocus disabled={working} onClick={close}>{started ? "Close" : "Cancel"}</button>
        <button type="button" className="button button-primary" disabled={working || blocked || !reportIds?.length}
          onClick={() => void clearAll()}>{busy ? "Clearing…" : started ? "Continue clearing" : "Clear all"}</button>
      </div>
    </div>
  </dialog>;
}

export default function ClearSummariesButton(props: Props) {
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const { blocked } = useMutationScope({ projectId: props.projectId, kinds: ["dismiss_job_completion_report", "create_job_completion_report_follow_up"] });
  return <>
    <button type="button" ref={trigger} className="button button-secondary" disabled={props.disabled || blocked}
      onClick={() => setOpen(true)}>Clear all</button>
    {open && <ClearSummariesDialog {...props} onClose={() => { setOpen(false); trigger.current?.focus(); }} />}
  </>;
}
