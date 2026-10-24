"use client";

import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import Link from "@/components/dashboard-link";
import { AssessmentView } from "@/components/knowledge-impact";
import { KnowledgePagination, KnowledgeReadError } from "@/components/knowledge-shared";
import { errorMessage } from "@/lib/api";
import { dashboardSessionId } from "@/lib/dashboard-session";
import type { DecisionChange } from "@/lib/decision-changes";
import { knowledgePath } from "@/lib/knowledge";
import { changeAssessmentsOptions, decisionChangeOptions, knowledgeKeys } from "@/lib/knowledge-queries";
import { mutationWorkKey, useMutationIntentRegistry, useMutationScope } from "@/lib/mutation-intent";
import type { WorkItem } from "@/lib/types";
import { validUuid } from "@/lib/wire-guards";
import { dashboardMutationActor } from "@/lib/work-events";

function ChangeScope({ work, projectId, changeId, disabled, execute }: {
  work: WorkItem; projectId: string; changeId: string; disabled: boolean;
  execute: (payload: object, targetProjectId: string) => Promise<DecisionChange | undefined>;
}) {
  const result = useQuery(decisionChangeOptions(projectId, changeId));
  if (result.isError && !result.data) return <KnowledgeReadError error={result.error} retry={() => void result.refetch()} />;
  if (!result.data) return <p role="status">Loading decision review scope…</p>;
  return <>
    {result.isError && <KnowledgeReadError error={result.error} retry={() => void result.refetch()} />}
    <LoadedScope key={changeId} work={work} change={result.data} disabled={disabled || result.isFetching} execute={execute} />
  </>;
}

function LoadedScope({ work, change, disabled, execute }: {
  work: WorkItem; change: DecisionChange; disabled: boolean;
  execute: (payload: object, targetProjectId: string) => Promise<DecisionChange | undefined>;
}) {
  const navigate = useNavigate();
  const registry = useMutationIntentRegistry();
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);
  const [assessmentPending, setAssessmentPending] = useState(false);
  const [reviewedVersion, setReviewedVersion] = useState<number | null>(null);
  const [admittedVersion, setAdmittedVersion] = useState<number | null>(null);
  const [successor, setSuccessor] = useState<DecisionChange | null>(null);
  useEffect(() => registry.subscribeConfirmed((confirmation) => {
    if (confirmation.kind === "decision_change_update"
      && confirmation.result.parent_change_id === change.id) setSuccessor(confirmation.result);
  }), [registry, change.id]);
  const episodes = useQuery(changeAssessmentsOptions(change.project_id, change.id, change.scope_version, offset));
  const driver = change.review_work_item_id === work.id;
  const closed = change.review_closed;
  const admissionRequired = closed || !change.review_work_item_id && change.pending_revision_count > 0;
  const successorId = successor?.id ?? change.successor_change_id;
  const acknowledged = change.scope_version === change.acknowledged_scope_version;
  const locked = disabled || assessmentPending;
  return <section className="impact-assessment" aria-label={`Decision change ${change.id}`}>
    <p><strong>{change.origin_reference ?? "Adopted decision assessments"}</strong> · Scope version {change.scope_version} · {change.revision_count} {change.revision_count === 1 ? "revision" : "revisions"} · {change.assessment_count} {change.assessment_count === 1 ? "assessment" : "assessments"}</p>
    <p className="field-hint">{acknowledged ? `Scope version ${change.scope_version} acknowledged.` : `Acknowledged version ${change.acknowledged_scope_version}; current scope needs review.`}</p>
    <p className="field-hint">Change <code>{change.id}</code>{change.parent_change_id && <> · Successor of <code>{change.parent_change_id}</code></>}</p>
    {change.pending_revision_count > 0 && <p>{change.pending_revision_count} accepted decision {change.pending_revision_count === 1 ? "revision is" : "revisions are"} retained awaiting owner admission. Admission adds their assessment obligations to the new review cycle.</p>}
    {!driver && change.review_work_item_id && <Link href={`/work-items?project=${change.review_work_project_id ?? change.project_id}&work=${change.review_work_item_id}`}>Open the review work item</Link>}
    {selected ? <>
      <button type="button" className="text-button" disabled={assessmentPending} onClick={() => setSelected(null)}>← All assessments in this change</button>
      <AssessmentView key={selected} projectId={change.project_id} id={selected} onPendingChange={setAssessmentPending}
        onDecision={(reference) => { void navigate({ to: "/knowledge", search: { project: change.project_id, decision: reference } }); }} />
    </> : episodes.isError ? <KnowledgeReadError error={episodes.error} retry={() => void episodes.refetch()} />
      : !episodes.data ? <p role="status">Loading attached assessments…</p> : <>
        <ol className="decision-history">{episodes.data.items.map((episode) => <li key={episode.id}>
          <button type="button" className="text-button" disabled={locked} onClick={() => setSelected(episode.id)}>{episode.before_reference} → {episode.after_reference}</button>
          <span>{episode.candidate_count} required {episode.candidate_count === 1 ? "candidate" : "candidates"} · {episode.unassessed_count ?? "Unknown"} unassessed · {episode.change_required_count ?? "Unknown"} require changes</span>
          <code>{episode.id}</code>
        </li>)}</ol>
        {!episodes.data.items.length && <p>No assessments in this scope.</p>}
        <KnowledgePagination page={episodes.data} noun="assessments" busy={locked || episodes.isFetching} onOffset={setOffset} />
      </>}
    {driver && !closed && !acknowledged && <div className="form-stack">
      <label className="toggle-field"><input type="checkbox" checked={reviewedVersion === change.scope_version} disabled={locked} onChange={(event) => setReviewedVersion(event.target.checked ? change.scope_version : null)} />I have inspected all assessments in scope version {change.scope_version}.</label>
      <p className="field-hint">Acknowledgement records the scope you reviewed. Required candidate dispositions and tracked remediation still govern completion.</p>
      <button type="button" className="button button-secondary" disabled={locked || reviewedVersion !== change.scope_version || !episodes.data || episodes.isError}
        onClick={() => void execute({ action: "acknowledge", change_id: change.id, expected_scope_version: change.scope_version }, change.project_id)}>Acknowledge scope version {change.scope_version}</button>
    </div>}
    {admissionRequired && !successorId && <div className="form-stack">
      <p>{closed ? "This review is closed." : "These retained decision revisions have no review driver."} A new substantive change or additional assessment obligation needs an owner-admitted review cycle.</p>
      <label className="toggle-field"><input type="checkbox" checked={admittedVersion === change.scope_version} disabled={locked} onChange={(event) => setAdmittedVersion(event.target.checked ? change.scope_version : null)} />Authorize a new review cycle for this change.</label>
      <button type="button" className="button button-secondary" disabled={locked || admittedVersion !== change.scope_version} onClick={async () => {
        const saved = await execute({ action: "admit", change_id: change.id, expected_scope_version: change.scope_version }, change.project_id);
        if (saved) setSuccessor(saved);
      }}>Admit a new review cycle</button>
    </div>}
    {successorId && <p role="status">New cycle admitted: <code>{successorId}</code>. {successor?.review_work_item_id && <Link href={`/work-items?project=${successor.review_work_project_id ?? successor.project_id}&work=${successor.review_work_item_id}`}>Open its review</Link>}</p>}
  </section>;
}

export default function DecisionChangePanel({ work, disabled, sourceProjectId }: { work: WorkItem; disabled: boolean; sourceProjectId?: unknown }) {
  const projectId = work.decision_review_project_id ?? (validUuid(sourceProjectId) ? sourceProjectId : work.project_id);
  const registry = useMutationIntentRegistry();
  const client = useQueryClient();
  const workKey = mutationWorkKey(work.project_id, work.id);
  const { blocked } = useMutationScope({ conflictKeys: [workKey] });
  const [assessmentIds, setAssessmentIds] = useState("");
  const [showLineage, setShowLineage] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const performing = useRef(false);
  const ids = [...new Set([work.decision_review_change_id, ...(work.decision_change_ids ?? [])].filter((id): id is string => Boolean(id)))];
  const inherited = ids.filter((id) => id !== work.decision_review_change_id);
  useEffect(() => registry.subscribeConfirmed((confirmation) => {
    if (confirmation.kind === "decision_change_update" && confirmation.intent.conflictKeys.includes(workKey)) {
      setError(""); setAssessmentIds("");
      void client.invalidateQueries({ queryKey: knowledgeKeys.root(confirmation.result.project_id) });
    }
  }), [registry, client, workKey, projectId]);

  async function execute(payload: object, targetProjectId: string) {
    if (performing.current || disabled || blocked) return;
    performing.current = true; setBusy(true); setError("");
    try {
      return await registry.execute({
        kind: "decision_change_update", slot: `decision-change:${work.project_id}:${work.id}`,
        projectId: targetProjectId, conflictKeys: [workKey], method: "POST",
        path: knowledgePath(targetProjectId, "/changes"),
        payload: { ...payload, ...dashboardMutationActor(dashboardSessionId()) }
      });
    } catch (failure) {
      setError(errorMessage(failure));
      void client.invalidateQueries({ queryKey: knowledgeKeys.root(targetProjectId) });
    } finally { performing.current = false; setBusy(false); }
  }

  const canAdopt = work.status === "pending" && !work.decision_review_change_id;
  if (!ids.length && !canAdopt) return null;
  return <details className="decision-change-panel" open={ids.length > 0}>
    <summary>Decision impact review scope</summary>
    {work.decision_review_change_id && <ChangeScope key={work.decision_review_change_id} work={work} projectId={work.decision_change_projects?.[work.decision_review_change_id] ?? projectId} changeId={work.decision_review_change_id} disabled={disabled || blocked || busy} execute={execute} />}
    {inherited.length > 0 && <details onToggle={(event) => setShowLineage(event.currentTarget.open)}>
      <summary>Inherited change lineage ({inherited.length})</summary>
      {showLineage && inherited.map((id) => <ChangeScope key={id} work={work} projectId={work.decision_change_projects?.[id] ?? projectId} changeId={id} disabled={disabled || blocked || busy} execute={execute} />)}
    </details>}
    {canAdopt && <details><summary>Adopt existing assessments into this work item</summary>
      <p>This makes this pending work the native review driver. Existing dispositions remain on their assessment episodes.</p>
      <form className="form-stack" onSubmit={(event) => {
        event.preventDefault();
        const values = assessmentIds.trim().split(/[\s,]+/).filter(Boolean);
        if (!values.length || values.length > 100 || !values.every(validUuid) || new Set(values).size !== values.length) {
          setError("Enter 1–100 unique assessment UUIDs, separated by spaces or commas."); return;
        }
        void execute({ action: "adopt", work_item_id: work.id, expected_work_version: work.version, assessment_ids: values }, work.project_id);
      }}>
        <label className="field">Assessment IDs<textarea rows={3} value={assessmentIds} disabled={disabled || blocked || busy} onChange={(event) => setAssessmentIds(event.target.value)} required /></label>
        <button type="submit" className="button button-secondary" disabled={disabled || blocked || busy || !assessmentIds.trim()}>Adopt assessments</button>
      </form>
    </details>}
    {error && <p role="alert" className="error-notice">{error}</p>}
  </details>;
}
