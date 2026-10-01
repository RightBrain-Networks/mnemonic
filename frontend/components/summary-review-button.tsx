"use client";

import { useEffect, useId, useRef, useState } from "react";
import { api, errorMessage, workItemPath } from "@/lib/api";
import { decodeWorkContext } from "@/lib/duplicate-handling";
import { dashboardSessionId } from "@/lib/dashboard-session";
import { dashboardMutationActor } from "@/lib/work-events";
import { mutationWorkKey, useMutationIntentRegistry, useMutationScope } from "@/lib/mutation-intent";
import { validReviewHandoff, type CodeReviewHandoff } from "@/lib/code-reviews";
import type { JobReportEnvelope } from "@/lib/types";
import CodeReviewHandoffEditor, { emptyReviewHandoff } from "@/components/code-review-handoff-editor";
import MutationRecoveryPanel from "@/components/mutation-recovery-panel";

export default function SummaryReviewButton({ item, onChanged, repositoryUrl }: {
  item: JobReportEnvelope; onChanged: () => void; repositoryUrl?: string | null;
}) {
  const { report, source_work_state: source } = item;
  const registry = useMutationIntentRegistry();
  const key = mutationWorkKey(report.project_id, report.work_item_id);
  const { blocked, intents } = useMutationScope({ conflictKeys: [key] });
  const [retrying, setRetrying] = useState("");
  const [open, setOpen] = useState(false);
  const [cold, setCold] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [queued, setQueued] = useState<"warm" | "cold" | null>(null);
  const [handoff, setHandoff] = useState<CodeReviewHandoff>(() => ({
    ...emptyReviewHandoff(repositoryUrl),
    handoff: { change_summary: report.summary, decisions: [], focus_areas: [], traps: [],
      validation_summary: "This human review request supplies no additional validation evidence." }
  }));
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const menuItem = useRef<HTMLButtonElement>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const performing = useRef(false);
  const requestedMode = useRef<"warm" | "cold" | null>(null);
  const alive = useRef(true);
  const menuId = useId();
  const titleId = useId();
  const reason = source.deleted ? "The original work was deleted."
    : source.canonical_work_item_id !== report.work_item_id ? "Review the canonical work item instead."
      : report.closeout_status !== "done" || source.status !== "done" ? "Code review requires completed work."
        : queued ? `${queued === "warm" ? "Warm" : "Cold"} review requested.` : null;
  const disabled = busy || blocked || Boolean(reason);

  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    if (!open) return;
    menuItem.current?.focus();
    const outside = (event: PointerEvent) => { if (!root.current?.contains(event.target as Node)) setOpen(false); };
    document.addEventListener("pointerdown", outside);
    return () => document.removeEventListener("pointerdown", outside);
  }, [open]);
  useEffect(() => { if (disabled) setOpen(false); }, [disabled]);
  useEffect(() => { if (cold) dialog.current?.showModal(); else dialog.current?.close(); }, [cold]);
  useEffect(() => registry.subscribeRecovered((intent) => {
    if (intent.kind !== "update_work" || !intent.conflictKeys.includes(key)) return;
    setError(""); setCold(false); setQueued(requestedMode.current); onChanged();
  }), [registry, key, onChanged]);

  async function requestReview(mode: "warm" | "cold") {
    if (performing.current || disabled || mode === "cold" && !validReviewHandoff(handoff)) return;
    performing.current = true; requestedMode.current = mode; setBusy(true); setError("");
    try {
      const path = workItemPath(report.project_id, report.work_item_id);
      const context = decodeWorkContext(await api<unknown>(`${path}/context?recent_limit=0&recent_event_limit=0`), report.project_id, report.work_item_id);
      if (!alive.current || registry.blocks([key])) return;
      await registry.execute({
        kind: "update_work", slot: `update-work:${report.project_id}:${report.work_item_id}`,
        projectId: report.project_id, conflictKeys: [key], method: "PATCH", path,
        payload: {
          expected_version: context.work_item.version, actor: dashboardMutationActor(dashboardSessionId()),
          request_code_review: true, request_code_review_mode: mode,
          request_code_review_checkpoint_id: report.completion_checkpoint_id,
          ...(mode === "cold" ? { code_review_handoff: handoff } : {})
        }
      });
      if (alive.current) { setQueued(mode); setCold(false); onChanged(); }
    } catch (failure) { if (alive.current) setError(errorMessage(failure)); }
    finally { performing.current = false; if (alive.current) setBusy(false); }
  }

  function closeForm() { if (!busy && !blocked) { setCold(false); trigger.current?.focus(); } }

  return <div className="summary-review-action">
    <div className="status-split-button" ref={root} onBlur={(event) => {
      if (!event.currentTarget.contains(event.relatedTarget as Node)) setOpen(false);
    }}>
      <button type="button" className="button defer-button status-split-primary" disabled={disabled}
        title={reason ?? "Request a warm code review of the completed work"}
        aria-label={`Warm review for ${report.work_title_at_closeout}`}
        onClick={() => void requestReview("warm")}>{busy ? "Requesting…" : queued ? "Review requested" : "Warm review"}</button>
      <button type="button" ref={trigger} className="button defer-button status-split-toggle" disabled={disabled}
        aria-label={`Choose review mode for ${report.work_title_at_closeout}`} aria-haspopup="menu" aria-expanded={open} aria-controls={menuId}
        onClick={() => setOpen((value) => !value)} onKeyDown={(event) => {
          if (["ArrowDown", "ArrowUp"].includes(event.key)) { event.preventDefault(); setOpen(true); }
        }}><span aria-hidden="true">⌄</span></button>
      {open && <div id={menuId} role="menu" className="status-action-menu" aria-label="Review mode" onKeyDown={(event) => {
        if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); setOpen(false); trigger.current?.focus(); }
        if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) { event.preventDefault(); menuItem.current?.focus(); }
      }}>
        <button type="button" role="menuitem" ref={menuItem} tabIndex={-1} onClick={() => { setOpen(false); setError(""); setCold(true); }}>Cold review</button>
      </div>}
    </div>
    {queued && <span className="sr-only" role="status">{queued === "cold" ? "Cold" : "Warm"} code review queued in Pending.</span>}
    {error && !cold && <p className="error-notice" role="alert">{error}</p>}
    {cold && <dialog ref={dialog} className="dialog" aria-labelledby={titleId} onCancel={(event) => { event.preventDefault(); closeForm(); }}>
      <div className="dialog-header"><h2 id={titleId}>Request cold review</h2><button type="button" className="button button-secondary" disabled={busy || blocked} onClick={closeForm}>Cancel</button></div>
      <div className="dialog-content">
        <p className="dialog-intro">Choose the repository and commit range for “{report.work_title_at_closeout}”. The reviewer receives this scope without the implementation notes.</p>
        <MutationRecoveryPanel intents={intents} retryingMutation={retrying} modal onRetry={(intent) => {
          setRetrying(intent.slot);
          void registry.retry(intent.slot).catch((failure) => {
            if (alive.current) setError(errorMessage(failure));
          }).finally(() => { if (alive.current) setRetrying(""); });
        }} />
        <form className="form-stack" onSubmit={(event) => { event.preventDefault(); void requestReview("cold"); }}>
          <CodeReviewHandoffEditor value={handoff} onChange={setHandoff} scopeOnly disabled={busy || blocked} />
          {error && <p className="error-notice" role="alert">{error}</p>}
          <div className="dialog-actions"><button type="submit" className="button button-primary" disabled={disabled || !validReviewHandoff(handoff)}>{busy ? "Requesting…" : "Create cold review"}</button></div>
        </form>
      </div>
    </dialog>}
  </div>;
}
