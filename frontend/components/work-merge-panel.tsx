"use client";

import { useRef, useState, type FormEvent } from "react";
import { OperationalBadge, StatusBadge } from "@/components/work-item-card";
import { useCanonicalWorkSearch } from "@/components/use-canonical-work-search";
import MutationRecoveryPanel, { type MutationRecoveryNotice } from "@/components/mutation-recovery-panel";
import { api, ApiError, errorMessage, workItemPath } from "@/lib/api";
import { currentContext } from "@/lib/current-context";
import { dashboardSessionId } from "@/lib/dashboard-session";
import {
  dashboardMergeInput,
  decodeWorkContext,
  duplicateMergeEligibilityReasons,
  mergeWorkPath
} from "@/lib/duplicate-handling";
import {
  mutationWorkKey,
  useMutationIntentRegistry,
  useMutationScope
} from "@/lib/mutation-intent";
import type {
  WorkContext,
  WorkMergeResult,
  WorkSearchHit
} from "@/lib/types";

const iconPaths = {
  close: "m6 6 12 12M6 18 18 6"
};

const mergeRecoveryNotice: MutationRecoveryNotice = {
  titles: {
    unresolved: "The merge outcome is unknown.",
    safety_conflict: "Merge retry safety conflict.",
    in_flight: "Merge request in flight."
  },
  description: "Keep this tab open. The exact request is frozen in memory for both work IDs.",
  retryLabel: "Retry exact pending merge"
};

function Icon({ name, size = 18 }: { name: keyof typeof iconPaths; size?: number }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={iconPaths[name]} /></svg>;
}

function DirectionPanel({
  kind,
  context
}: {
  kind: "source" | "destination";
  context: WorkContext;
}) {
  const source = kind === "source";
  const directionId = `${kind}-merge-direction-label`;
  const headingId = `${kind}-merge-direction-title`;
  const checkpoint = currentContext(context);
  return <section
    className={`merge-direction-panel merge-direction-${kind}`}
    aria-labelledby={`${directionId} ${headingId}`}
    data-review-direction={kind}
  >
    <span className="section-label" id={directionId}>{source ? "SOURCE — BECOMES IMMUTABLE" : "DESTINATION — REMAINS CANONICAL"}</span>
    <h3 id={headingId}><bdi dir="auto">{context.work_item.title}</bdi></h3>
    <span className="mono merge-full-id">{context.work_item.id}</span>
    <div className="merge-direction-badges">
      <StatusBadge status={context.work_item.status} readiness={context.readiness} />
      <OperationalBadge readiness={context.readiness} />
      <span>v{context.work_item.version}</span>
    </div>
    <p>{context.work_item.summary}</p>
    <dl>
      <div><dt>Current context</dt><dd>{checkpoint.prompt}</dd></div>
      <div><dt>Human questions</dt><dd>{context.readiness.unresolved_gate_count} unresolved</dd></div>
      <div><dt>Lease state</dt><dd>{context.duplicate_merge_eligibility.source_lease_state}</dd></div>
      <div><dt>Review revision</dt><dd className="mono">v{context.merge_review_revision.work_version} · {context.merge_review_revision.context_checkpoint_id} · {context.merge_review_revision.work_event_count} events</dd></div>
    </dl>
  </section>;
}

export default function WorkMergePanel({
  source,
  recoveryVisible,
  onClose,
  onMerged,
  onSourceChanged
}: {
  source: WorkContext;
  recoveryVisible: boolean;
  onClose: () => void;
  onMerged: (result: WorkMergeResult) => void | Promise<void>;
  onSourceChanged: () => void | Promise<void>;
}) {
  const reviewRequest = useRef(0);
  const registry = useMutationIntentRegistry();
  const slot = `merge-work:${source.work_item.project_id}:${source.work_item.id}`;
  const { intents, blocked: dispatched } = useMutationScope({ slot }, registry);
  const mergeIntent = intents[0];
  const [query, setQuery] = useState("");
  const { searchedQuery, page: results, searching, error: searchError } = useCanonicalWorkSearch({
    projectId: source.work_item.project_id,
    excludedWorkId: source.work_item.id,
    query
  });
  const [selected, setSelected] = useState<WorkSearchHit | null>(null);
  const [review, setReview] = useState<{
    source: WorkContext;
    destination: WorkContext;
  } | null>(null);
  const [reviewing, setReviewing] = useState(false);
  const [reviewError, setReviewError] = useState("");
  const [rationale, setRationale] = useState("");
  const [permanent, setPermanent] = useState(false);
  const [saving, setSaving] = useState(false);
  const [stale, setStale] = useState(false);

  async function loadReview(destination: WorkSearchHit) {
    const requestId = ++reviewRequest.current;
    setSelected(destination);
    setReview(null);
    setReviewError("");
    setReviewing(true);
    setStale(false);
    setPermanent(false);
    try {
      const projectId = source.work_item.project_id;
      const [sourceValue, destinationValue] = await Promise.all([
        api<unknown>(`${workItemPath(projectId, source.work_item.id)}/context?recent_limit=5&recent_event_limit=10`),
        api<unknown>(`${workItemPath(projectId, destination.summary.work_item.id)}/context?recent_limit=5&recent_event_limit=10`)
      ]);
      const sourceContext = decodeWorkContext(sourceValue, projectId, source.work_item.id);
      const destinationContext = decodeWorkContext(
        destinationValue,
        projectId,
        destination.summary.work_item.id
      );
      if (sourceContext.canonical.is_duplicate || destinationContext.canonical.is_duplicate) {
        throw new Error("Merge review requires two current canonical roots.");
      }
      if (reviewRequest.current !== requestId) return;
      setReview({ source: sourceContext, destination: destinationContext });
    } catch (cause) {
      if (reviewRequest.current === requestId) setReviewError(errorMessage(cause));
    } finally {
      if (reviewRequest.current === requestId) setReviewing(false);
    }
  }

  async function finish(result: WorkMergeResult) {
    setSaving(false);
    await onMerged(result);
  }

  async function handleMergeFailure(cause: unknown) {
    if (cause instanceof ApiError && cause.code === "work_already_duplicate") {
      setPermanent(false);
      setReview(null);
      setSelected(null);
      setSaving(false);
      await onSourceChanged();
      return;
    }
    if (
      cause instanceof ApiError
      && (cause.code === "duplicate_context_changed"
        || cause.code === "duplicate_destination_not_canonical")
    ) {
      setStale(true);
      setPermanent(false);
    }
    setReviewError(errorMessage(cause));
    setSaving(false);
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!review || !permanent || stale || saving) return;
    setSaving(true);
    setReviewError("");
    try {
      const payload = dashboardMergeInput(
        review.destination.work_item.id,
        review.source.merge_review_revision,
        review.destination.merge_review_revision,
        rationale,
        dashboardSessionId()
      );
      const result = await registry.execute({
        kind: "merge_work",
        slot,
        projectId: review.source.work_item.project_id,
        conflictKeys: [
          mutationWorkKey(review.source.work_item.project_id, review.source.work_item.id),
          mutationWorkKey(review.destination.work_item.project_id, review.destination.work_item.id)
        ],
        method: "POST",
        path: mergeWorkPath(review.source.work_item.project_id, review.source.work_item.id),
        payload
      });
      await finish(result);
    } catch (cause) {
      await handleMergeFailure(cause);
    }
  }

  async function retryExact() {
    if (!mergeIntent || mergeIntent.state !== "unresolved" || saving) return;
    setSaving(true);
    setReviewError("");
    try {
      await finish(await registry.retry<"merge_work">(slot));
    } catch (cause) {
      await handleMergeFailure(cause);
    }
  }

  const reasons = review
    ? duplicateMergeEligibilityReasons(review.source.duplicate_merge_eligibility)
    : [];
  const blocked = reasons.length > 0;
  const cannotClose = saving || dispatched;
  return <section className="merge-panel" aria-label="Merge as duplicate">
    <div className="merge-panel-heading">
      <div><span className="section-label merge-eyebrow">IRREVERSIBLE MERGE</span><h4>Merge as duplicate</h4></div>
      <button type="button" className="icon-button" aria-label="Close merge" disabled={cannotClose} onClick={onClose}><Icon name="close" /></button>
    </div>
    {recoveryVisible && <MutationRecoveryPanel
      intents={intents}
      retryingMutation={saving ? slot : ""}
      onRetry={() => void retryExact()}
      notice={mergeRecoveryNotice}
    />}
    <div className="merge-pick-grid">
      <div className="merge-direction-panel merge-direction-source" data-direction="source">
        <span className="section-label">SOURCE · BECOMES AN IMMUTABLE DUPLICATE AUDIT</span>
        <h3><bdi dir="auto">{source.work_item.title}</bdi></h3>
        <span className="mono merge-full-id">{source.work_item.id}</span>
        <p>Its checkpoints, events, and relationships are retained verbatim under this exact ID and never blended into the canonical record.</p>
      </div>
      <div className="merge-direction-panel merge-direction-destination" data-direction="destination">
        <span className="section-label">CANONICAL DESTINATION</span>
        <input autoFocus type="search" className="merge-destination-search" aria-label="Find a canonical destination" value={query} maxLength={500} disabled={dispatched} placeholder="Search titles, summaries, or checkpoints…" onChange={(event) => { reviewRequest.current += 1; setReviewing(false); setQuery(event.target.value); setSelected(null); setReview(null); }} />
        {searching && <div role="status">Searching canonical work…</div>}
        {searchError && <div className="error-notice" role="alert"><p>{searchError}</p></div>}
        {results?.items.length ? <div className="counterpart-results" role="listbox" aria-label="Canonical merge destinations">{results.items.map((item) => {
          const selectedItem = selected?.summary.work_item.id === item.summary.work_item.id;
          return <button className={selectedItem ? "selected" : ""} key={item.summary.work_item.id} type="button" role="option" aria-selected={selectedItem} disabled={dispatched} onClick={() => void loadReview(item)}><span><strong><bdi dir="auto">{item.summary.work_item.title}</bdi></strong><span>{item.summary.work_item.summary}</span><span className="mono">{item.summary.work_item.id}</span></span><StatusBadge status={item.summary.work_item.status} readiness={item.summary.readiness} /></button>;
        })}</div> : null}
        {!searching && searchedQuery && results && !results.items.length && <p>No other canonical work matches.</p>}
      </div>
    </div>
    {reviewing && <div className="loading-state" role="status"><span className="spinner" />Loading both exact review contexts…</div>}
    {review && <form className="form-stack merge-review" onSubmit={(event) => void submit(event)}>
      <div className="merge-direction-grid" aria-label="Permanent merge direction">
        <DirectionPanel kind="source" context={review.source} />
        <DirectionPanel kind="destination" context={review.destination} />
      </div>
      <section className="merge-eligibility" aria-labelledby="merge-eligibility-title">
        <h3 id="merge-eligibility-title">Source eligibility</h3>
        <dl>
          <div><dt>Incident blockers</dt><dd>{review.source.duplicate_merge_eligibility.incident_blocks_count}</dd></div>
          <div><dt>Incident parent/child</dt><dd>{review.source.duplicate_merge_eligibility.incident_parent_child_count}</dd></div>
          <div><dt>Unresolved gate</dt><dd>{review.source.duplicate_merge_eligibility.has_unresolved_gate ? "Yes" : "No"}</dd></div>
          <div><dt>Lease</dt><dd>{review.source.duplicate_merge_eligibility.source_lease_state}</dd></div>
        </dl>
        {blocked && <ul className="terminal-action-note">{reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>}
        {blocked && <button type="button" className="text-link merge-reconcile-link" onClick={onClose}>Return to the source to reconcile these conflicts</button>}
      </section>
      <label className="field">Merge rationale<textarea rows={5} required maxLength={4000} disabled={dispatched} value={rationale} onChange={(event) => setRationale(event.target.value)} /><span className="field-hint">Stored verbatim on both immutable merge decision events.</span></label>
      <label className="merge-permanence"><input type="checkbox" checked={permanent} disabled={dispatched || stale} onChange={(event) => setPermanent(event.target.checked)} /><span>I have read both exact work contexts. This merge is permanent: the source becomes a duplicate audit record and cannot be reopened.</span></label>
      {stale && selected && <div className="error-notice" role="alert"><p>The reviewed source or destination changed. Refetch both contexts to create a new merge operation.</p><button type="button" className="button button-secondary" disabled={reviewing} onClick={() => void loadReview(selected)}>Refetch both contexts</button></div>}
      {reviewError && !stale && <div className="error-notice" role="alert"><p>{reviewError}</p></div>}
      <div className="merge-panel-actions">
        <button type="button" className="button button-secondary" disabled={cannotClose} onClick={onClose}>Cancel</button>
        <button type="submit" className="button button-danger" disabled={saving || dispatched || stale || blocked || !permanent || !rationale.trim()}>{saving ? "Merging…" : "Merge permanently"}</button>
      </div>
    </form>}
    {reviewError && !review && <div className="error-notice" role="alert"><p>{reviewError}</p></div>}
  </section>;
}
