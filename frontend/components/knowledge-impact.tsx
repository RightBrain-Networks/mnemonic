"use client";

import { useRef, useState, type FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "@/components/dashboard-link";
import { DecisionStateBadge, KnowledgePagination, KnowledgeReadError } from "@/components/knowledge-shared";
import { ScopeEvidence } from "@/components/knowledge-scope";
import { dashboardSessionId } from "@/lib/dashboard-session";
import { dashboardMutationActor } from "@/lib/work-events";
import { ApiError, errorMessage } from "@/lib/api";
import { artifactLibraryPath } from "@/lib/artifacts";
import { formatDateTime } from "@/lib/display-time";
import { knowledgeJson, knowledgePath } from "@/lib/knowledge";
import { decodeDisposition, impactDispositions, type AssessmentDetail, type ImpactAssessment, type ImpactCandidate, type ImpactCoverage, type ImpactDisposition, type ImpactDispositionName } from "@/lib/knowledge-impact";
import { assessmentOptions, assessmentsOptions, decisionImpactOptions, dispositionHistoryOptions, knowledgeKeys } from "@/lib/knowledge-queries";
import { validUuid } from "@/lib/wire-guards";

function AssessmentStatus({ assessment }: { assessment: ImpactAssessment }) {
  if (assessment.status === undefined) return <p className="field-hint">Assessment progress not recorded.</p>;
  return <p className={`impact-assessment-status impact-assessment-${assessment.status}`}><strong>{assessment.status === "resolved" ? "All candidates assessed" : assessment.status === "change-required" ? "Changes required" : "Assessment pending"}</strong> · {assessment.unassessed_count} unassessed · {assessment.change_required_count} require changes</p>;
}
export function CoverageNotice({ coverage }: { coverage: ImpactCoverage }) {
  return <aside className="impact-coverage" aria-label="Impact coverage">
    <strong>{coverage.traversal_complete ? "Recorded graph traversal complete" : "Recorded graph traversal incomplete"}</strong>
    {coverage.snapshot_at && <p>Evidence snapshot: {formatDateTime(coverage.snapshot_at)}. Later graph changes and citations are not included.</p>}
    <p>Provenance is incomplete. Related links do not establish dependencies; Git history is not verified.</p>
    <details><summary>Coverage limits and notes</summary>
      <p>{coverage.visited_revisions} revisions visited · depth limit {coverage.max_depth} · candidate limit {coverage.candidate_limit}. One deterministic shortest path per revision is shown.</p>
      {!!coverage.notes?.length && <ul>{coverage.notes.map((note, index) => <li key={index}>{note}</li>)}</ul>}
    </details>
    {!!coverage.reasons.length && <ul>{coverage.reasons.map((reason, index) => <li key={index}>{({ dependency_edge_limit: "Some dependency links exceed the traversal limit.", candidate_limit: "Additional candidates exceed the result limit.", depth_limit: "Dependencies continue beyond the selected depth.", related_edge_limit: "Some related links exceed the traversal limit." } as Record<string, string>)[reason] ?? reason}</li>)}</ul>}
  </aside>;
}
function CandidateEvidence({ candidate, projectId, onDecision, disabled = false, snapshot = false }: { candidate: ImpactCandidate; projectId: string; onDecision: (reference: string) => void; disabled?: boolean; snapshot?: boolean }) {
  return <>
    <div className="impact-candidate-heading"><button type="button" className="text-button" disabled={disabled} onClick={() => onDecision(candidate.reference)}>{candidate.reference}</button><DecisionStateBadge state={candidate.state} /><span>{candidate.is_current ? snapshot ? "Current at assessment" : "Current revision" : snapshot ? "Historical at assessment" : "Historical revision"}</span></div>
    <strong>{candidate.title}</strong>
    <p>{candidate.relationship === "origin" ? "Starting decision" : candidate.relationship === "depends-on" ? "Recorded dependency" : "Related only · dependency meaning unknown"}</p>
    <ol className="impact-path" aria-label={`Evidence path to ${candidate.reference}`}>{candidate.path.map((reference, index) => <li key={`${index}-${reference}`}><button type="button" className="text-button" disabled={disabled} onClick={() => onDecision(reference)}>{reference}</button></li>)}</ol>
    {candidate.rationale && <p className="impact-rationale">{candidate.rationale}</p>}
    <details><summary>Registered evidence · {candidate.scopes.length} {candidate.scopes.length === 1 ? "commit" : "commits"}</summary>
      {!candidate.scopes.length && <p>No commits recorded for this revision.</p>}
      <ul className="impact-scope-list">{candidate.scopes.map((entry) => <li key={entry.id}><ScopeEvidence projectId={projectId} entry={entry} /></li>)}</ul>
      {!!candidate.related_work_item_ids.length && <p>Related work: {candidate.related_work_item_ids.map((id) => <Link key={id} className="artifact-work-link" href={`/work-items?project=${projectId}&work=${id}`}>{id}</Link>)}</p>}
      {!!candidate.related_artifact_ids.length && <p>Related artifacts: {candidate.related_artifact_ids.map((id) => <Link key={id} className="artifact-work-link" href={`${artifactLibraryPath(projectId)}&artifact=${id}`}>{id}</Link>)}</p>}
    </details>
    {candidate.evidence_truncated && <p className="error-notice">Evidence for this candidate is truncated. Open the decision’s code scope and details for more records.</p>}
  </>;
}
export function DecisionImpactView({ projectId, decisionId, revision, onDecision }: { projectId: string; decisionId: string; revision: number; onDecision: (reference: string) => void }) {
  const [offset, setOffset] = useState(0);
  const result = useQuery(decisionImpactOptions(projectId, decisionId, revision, offset));
  return result.isError ? <KnowledgeReadError error={result.error} retry={() => void result.refetch()} /> : result.isPending ? <p role="status">Following recorded dependencies…</p> : <>
    <CoverageNotice coverage={result.data.coverage} />
    <ol className="impact-candidates">{result.data.items.map((candidate) => <li key={candidate.reference}><CandidateEvidence candidate={candidate} projectId={projectId} onDecision={onDecision} /></li>)}</ol>
    {!result.data.items.length && <p>No candidates on this page.</p>}
    <KnowledgePagination page={result.data} noun="impact candidates" busy={result.isFetching} onOffset={setOffset} />
  </>;
}
export function DecisionAssessments({ projectId, decisionId, onDecision, onPendingChange }: { projectId: string; decisionId: string; onDecision: (reference: string) => void; onPendingChange: (pending: boolean) => void }) {
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const list = useQuery(assessmentsOptions(projectId, decisionId, offset));
  const changePending = (value: boolean) => { setPending(value); onPendingChange(value); };
  if (selected) return <><button type="button" className="text-button" disabled={pending} onClick={() => setSelected(null)}>← All assessments</button><AssessmentView key={selected} projectId={projectId} id={selected} onDecision={onDecision} onPendingChange={changePending} /></>;
  return list.isError ? <KnowledgeReadError error={list.error} retry={() => void list.refetch()} /> : list.isPending ? <p role="status">Loading assessments…</p> : <>
    <p className="artifact-history-note">Revision changes and supersessions retain their before/after evidence and affected candidates. Dispositions add to the assessment history; they do not change decision documents.</p>
    {!list.data.items.length && <p>No assessments recorded for this decision.</p>}
    <ol className="decision-history">{list.data.items.map((item) => <li key={item.id}><button type="button" className="text-button" onClick={() => setSelected(item.id)}>{item.before_reference} → {item.after_reference}</button><span>{item.trigger === "revision" ? "Revision change" : `${item.extent ?? "Unknown extent"} supersession`} · {item.candidate_count} candidates</span>{item.reason && <p>{item.reason}</p>}<AssessmentStatus assessment={item} /><time dateTime={item.created_at}>{formatDateTime(item.created_at)}</time></li>)}</ol>
    <KnowledgePagination page={list.data} noun="assessments" busy={list.isFetching} onOffset={setOffset} />
  </>;
}
function DispositionRecord({ entry, projectId }: { entry: ImpactDisposition; projectId: string }) {
  return <div className="impact-disposition"><strong>{entry.disposition.replaceAll("-", " ")}</strong><p>{entry.reason}</p>{entry.remediation_work_item_id && <Link href={`/work-items?project=${projectId}&work=${entry.remediation_work_item_id}`}>Remediation work {entry.remediation_work_item_id}</Link>}<span>Record {entry.version} · {formatDateTime(entry.created_at)} · {entry.actor.actor_client} · {entry.actor.actor_session_id}{entry.actor.actor_model ? ` · ${entry.actor.actor_model}` : ""}</span></div>;
}
function AssessmentView({ projectId, id, onDecision, onPendingChange }: { projectId: string; id: string; onDecision: (reference: string) => void; onPendingChange: (pending: boolean) => void }) {
  const [offset, setOffset] = useState(0);
  const [historyOffset, setHistoryOffset] = useState(0);
  const [showHistory, setShowHistory] = useState(false);
  const [candidate, setCandidate] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const result = useQuery({ ...assessmentOptions(projectId, id, offset), enabled: !pending });
  const history = useQuery(dispositionHistoryOptions(projectId, id, historyOffset, showHistory));
  const changePending = (value: boolean) => { setPending(value); onPendingChange(value); };
  if (result.isError && !result.data) return <KnowledgeReadError error={result.error} retry={() => void result.refetch()} />;
  if (!result.data) return <p role="status">Loading assessment…</p>;
  const detail = result.data;
  return <div className="impact-assessment">
    {result.isError && !pending && <KnowledgeReadError error={result.error} retry={() => void result.refetch()} />}
    <h3>{detail.before_reference} → {detail.after_reference}</h3>
    <AssessmentStatus assessment={detail} />
    <p>{detail.trigger === "revision" ? "Revision change" : `${detail.extent ?? "Unknown extent"} supersession`}{detail.reason && ` · ${detail.reason}`}</p>
    <div className="settings-actions"><button className="text-button" disabled={pending} onClick={() => onDecision(detail.before_reference)}>Read before</button><button className="text-button" disabled={pending} onClick={() => onDecision(detail.after_reference)}>Read after</button></div>
    <details className="impact-diff" open><summary>Before / after diff · {detail.diff.changed_fields.join(", ") || "No changed fields"}</summary>
      <pre tabIndex={0} aria-label="Decision revision diff">{detail.diff.unified_diff || "No text changes. See changed metadata fields above."}</pre>
      {detail.diff.truncated && <p className="error-notice">The diff is truncated. Open both retained revisions for the complete documents.</p>}
      <dl><dt>Before SHA-256</dt><dd className="mono break-all">{detail.diff.before_sha256}</dd><dt>After SHA-256</dt><dd className="mono break-all">{detail.diff.after_sha256}</dd></dl>
    </details>
    <CoverageNotice coverage={detail.coverage} />
    <ol className="impact-candidates">{detail.candidates.map((item) => { const latest = detail.dispositions.find((entry) => entry.candidate_reference === item.reference); return <li key={item.reference}>
      <CandidateEvidence candidate={item} projectId={projectId} onDecision={onDecision} disabled={pending} snapshot />
      {latest ? <DispositionRecord entry={latest} projectId={projectId} /> : <p className="field-hint">No disposition recorded.</p>}
      {candidate === item.reference ? <DispositionForm key={item.reference} detail={detail} reference={item.reference} onPendingChange={changePending} onClose={() => setCandidate(null)} />
        : <button type="button" className="button button-secondary" disabled={pending} onClick={() => setCandidate(item.reference)}>Record disposition for {item.reference}</button>}
    </li>; })}</ol>
    <KnowledgePagination page={{ items: detail.candidates, total: detail.candidate_count, limit: detail.limit, offset: detail.offset, next_offset: detail.next_offset }} noun="candidates" busy={pending || result.isFetching} onOffset={(value) => { setCandidate(null); setOffset(value); }} />
    <details onToggle={(event) => setShowHistory(event.currentTarget.open)}><summary>Disposition history · {detail.disposition_history_total} records</summary>
      <p className="field-hint">Every submitted disposition remains available, including earlier conclusions.</p>
      {history.isError ? <KnowledgeReadError error={history.error} retry={() => void history.refetch()} /> : history.isPending ? <p role="status">Loading disposition history…</p> : <><ol className="decision-history">{history.data.items.map((entry) => <li key={entry.id}><code>{entry.candidate_reference}</code><DispositionRecord entry={entry} projectId={projectId} /></li>)}</ol><KnowledgePagination page={history.data} noun="dispositions" busy={pending || history.isFetching} onOffset={setHistoryOffset} /></>}
    </details>
  </div>;
}
type Intent = { actor: { actor_client: string; actor_session_id: string; actor_model?: string | null }; body: string; candidate_reference: string; expected_version: number; disposition: ImpactDispositionName; reason: string; remediation_work_item_id: string | null };
function DispositionForm({ detail, reference, onPendingChange, onClose }: { detail: AssessmentDetail; reference: string; onPendingChange: (pending: boolean) => void; onClose: () => void }) {
  const client = useQueryClient();
  const [expectedVersion, setExpectedVersion] = useState(detail.version);
  const [reviewRequired, setReviewRequired] = useState(false);
  const stale = expectedVersion !== detail.version;
  const [disposition, setDisposition] = useState<ImpactDispositionName | "">("");
  const [reason, setReason] = useState("");
  const [work, setWork] = useState("");
  const [pending, setPending] = useState<Intent | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const dispatching = useRef(false);
  async function execute(intent: Intent) {
    if (dispatching.current) return;
    dispatching.current = true; setBusy(true); setError(""); setPending(intent); onPendingChange(true);
    try {
      const saved = decodeDisposition(await knowledgeJson(knowledgePath(detail.project_id, `/assessments/${detail.id}/dispositions`), { method: "POST", body: intent.body }), detail.project_id, detail.id);
      if (saved.candidate_reference !== intent.candidate_reference || saved.version !== intent.expected_version + 1
        || saved.disposition !== intent.disposition || saved.reason !== intent.reason || saved.remediation_work_item_id !== intent.remediation_work_item_id
        || saved.actor.actor_client !== intent.actor.actor_client || saved.actor.actor_session_id !== intent.actor.actor_session_id
        || (saved.actor.actor_model ?? null) !== (intent.actor.actor_model ?? null)) throw new Error("The returned disposition does not match this action. Retry the saved action.");
      setPending(null); onPendingChange(false);
      await client.invalidateQueries({ queryKey: knowledgeKeys.root(detail.project_id) }); onClose();
    } catch (failure) {
      if (failure instanceof ApiError && failure.status >= 400 && failure.status < 500 && failure.status !== 408) {
        setPending(null); onPendingChange(false);
        if (failure.status === 409) {
          setReviewRequired(true);
          void client.invalidateQueries({ queryKey: knowledgeKeys.root(detail.project_id) });
        }
      }
      setError(errorMessage(failure));
    } finally { dispatching.current = false; setBusy(false); }
  }
  function submit(event: FormEvent) {
    event.preventDefault();
    if (pending) return;
    if (stale || reviewRequired) { setError("Review the newer assessment dispositions before saving this draft."); return; }
    if (!disposition || !reason.trim() || reason.includes("\0") || work.trim() && !validUuid(work.trim())) { setError("Choose a disposition, give a reason, and use a valid work item UUID if linking remediation."); return; }
    const value = { candidate_reference: reference, expected_version: expectedVersion, disposition, reason: reason.trim(), remediation_work_item_id: work.trim() || null, client_operation_id: crypto.randomUUID(), actor: dashboardMutationActor(dashboardSessionId()) };
    void execute({ ...value, body: JSON.stringify(value) });
  }
  return <form className="form-stack impact-disposition-form" onSubmit={submit} aria-label={`Disposition for ${reference}`}>
    {(stale || reviewRequired) && !pending && <div className="impact-review-notice" role="alert">
      <p>This assessment has newer dispositions. Your draft still refers to version {expectedVersion}. Review the latest conclusions above before accepting version {detail.version}.</p>
      <button type="button" className="button button-secondary" disabled={detail.version === expectedVersion} onClick={() => { setExpectedVersion(detail.version); setReviewRequired(false); setError(""); }}>Use reviewed assessment version {detail.version}</button>
    </div>}
    <fieldset disabled={Boolean(pending)}><legend>Append a disposition</legend>
      <label className="field">Disposition<select value={disposition} required onChange={(event) => setDisposition(event.target.value as ImpactDispositionName)}><option value="">Choose a conclusion</option>{impactDispositions.map((value) => <option value={value} key={value}>{value.replaceAll("-", " ")}</option>)}</select></label>
      <label className="field">Reason<textarea rows={3} maxLength={4000} required value={reason} onChange={(event) => setReason(event.target.value)} /></label>
      <label className="field">Remediation work item ID (optional)<input value={work} onChange={(event) => setWork(event.target.value)} placeholder="Work item UUID" /></label>
    </fieldset>
    {error && <div className="error-notice" role="alert"><p>{error}</p>{pending && <><p>The outcome is unconfirmed. Retry this saved action before leaving.</p><button type="button" className="button button-secondary" disabled={busy} onClick={() => void execute(pending)}>Retry pending disposition</button></>}</div>}
    <div className="settings-actions"><button type="button" className="button button-secondary" disabled={Boolean(pending)} onClick={onClose}>Cancel disposition</button><button type="submit" className="button button-primary" disabled={Boolean(pending) || stale || reviewRequired}>{busy ? "Saving…" : "Save disposition"}</button></div>
  </form>;
}
