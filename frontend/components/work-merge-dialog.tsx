"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { OperationalBadge, StatusBadge } from "@/components/work-item-card";
import { api, ApiError, errorMessage, workItemPath } from "@/lib/api";
import { currentContext } from "@/lib/current-context";
import { dashboardSessionId } from "@/lib/dashboard-session";
import {
  dashboardMergeInput,
  decodeWorkContext,
  decodeWorkSearchPage,
  duplicateMergeEligibilityReasons,
  mergeWorkPath
} from "@/lib/duplicate-handling";
import {
  mutationWorkKey,
  useMutationIntentRegistry,
  useMutationIntents
} from "@/lib/mutation-intent";
import { workSearchParams } from "@/lib/work-item-search";
import type {
  Page,
  WorkContext,
  WorkMergeResult,
  WorkSearchHit
} from "@/lib/types";

const DESTINATION_LIMIT = 10;

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
    data-direction={kind}
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

export default function WorkMergeDialog({
  source,
  onClose,
  onMerged,
  onSourceChanged
}: {
  source: WorkContext;
  onClose: () => void;
  onMerged: (result: WorkMergeResult) => void | Promise<void>;
  onSourceChanged: () => void | Promise<void>;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const reviewRequest = useRef(0);
  const registry = useMutationIntentRegistry();
  const intents = useMutationIntents(registry);
  const slot = `merge-work:${source.work_item.project_id}:${source.work_item.id}`;
  const mergeIntent = intents.find((intent) => intent.slot === slot);
  const dispatched = Boolean(mergeIntent && mergeIntent.state !== "prepared");
  const [query, setQuery] = useState("");
  const [searchedQuery, setSearchedQuery] = useState("");
  const [results, setResults] = useState<Page<WorkSearchHit> | null>(null);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState("");
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

  useEffect(() => {
    const dialog = dialogRef.current;
    if (dialog && !dialog.open) dialog.showModal();
    return () => { if (dialog?.open) dialog.close(); };
  }, []);

  useEffect(() => {
    const timer = setTimeout(() => setSearchedQuery(query.trim()), 250);
    return () => clearTimeout(timer);
  }, [query]);

  useEffect(() => {
    if (!searchedQuery) {
      setResults(null);
      setSearchError("");
      return;
    }
    const controller = new AbortController();
    const params = workSearchParams({
      status: "all",
      sort: "updated",
      limit: DESTINATION_LIMIT,
      offset: 0,
      query: searchedQuery,
      duplicateScope: "canonical"
    });
    setSearching(true);
    setSearchError("");
    api<unknown>(`${workItemPath(source.work_item.project_id)}?${params}`, {
      signal: controller.signal
    }).then((value) => {
      if (controller.signal.aborted) return;
      const page = decodeWorkSearchPage(value, source.work_item.project_id, {
        duplicateScope: "canonical",
        query: searchedQuery,
        expectedLimit: DESTINATION_LIMIT,
        expectedOffset: 0
      });
      setResults({
        ...page,
        items: page.items.filter((item) => (
          item.summary.work_item.id.toLowerCase() !== source.work_item.id.toLowerCase()
        ))
      });
    }).catch((cause) => {
      if (!controller.signal.aborted) setSearchError(errorMessage(cause));
    }).finally(() => {
      if (!controller.signal.aborted) setSearching(false);
    });
    return () => controller.abort();
  }, [searchedQuery, source.work_item.id, source.work_item.project_id]);

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
  return <dialog
    ref={dialogRef}
    className="dialog dialog-wide merge-dialog"
    aria-labelledby="merge-dialog-title"
    onCancel={(event) => { event.preventDefault(); if (!cannotClose) onClose(); }}
  >
    <div className="dialog-header">
      <div><span className="section-label">IRREVERSIBLE CANONICAL DECISION</span><h2 id="merge-dialog-title">Merge duplicate work</h2></div>
      <button type="button" className="icon-button" aria-label="Close merge dialog" disabled={cannotClose} onClick={onClose}>×</button>
    </div>
    <div className="dialog-content form-stack">
      <p className="dialog-intro">Choose a canonical destination, then review both exact contexts. The source remains addressable only as immutable audit history.</p>
      {mergeIntent && mergeIntent.state !== "prepared" && <div className="mutation-recovery" role="alert">
        <strong>{mergeIntent.state === "unresolved" ? "The merge outcome is unknown." : mergeIntent.state === "safety_conflict" ? "Merge retry safety conflict." : "Merge request in flight."}</strong>
        <span>Keep this tab open. The exact request is frozen in memory for both work IDs.</span>
        {mergeIntent.state === "unresolved" && <button type="button" className="button button-secondary" disabled={saving} onClick={() => void retryExact()}>{saving ? "Retrying…" : "Retry exact pending merge"}</button>}
      </div>}
      <label className="field">Find a canonical destination<input autoFocus type="search" value={query} maxLength={500} disabled={dispatched} placeholder="Search titles, summaries, or checkpoints…" onChange={(event) => { reviewRequest.current += 1; setReviewing(false); setQuery(event.target.value); setSelected(null); setReview(null); }} /></label>
      {searching && <div role="status">Searching canonical work…</div>}
      {searchError && <div className="error-notice" role="alert"><p>{searchError}</p></div>}
      {results?.items.length ? <div className="counterpart-results" role="group" aria-label="Canonical merge destinations">{results.items.map((item) => {
        const selectedItem = selected?.summary.work_item.id === item.summary.work_item.id;
        return <button className={selectedItem ? "selected" : ""} key={item.summary.work_item.id} type="button" aria-pressed={selectedItem} disabled={dispatched} onClick={() => void loadReview(item)}><span><strong><bdi dir="auto">{item.summary.work_item.title}</bdi></strong><span>{item.summary.work_item.summary}</span><span className="mono">{item.summary.work_item.id}</span></span><StatusBadge status={item.summary.work_item.status} readiness={item.summary.readiness} /></button>;
      })}</div> : null}
      {!searching && searchedQuery && results && !results.items.length && <p>No other canonical work matches.</p>}
      {reviewing && <div className="loading-state" role="status"><span className="spinner" />Loading both exact review contexts…</div>}
      {review && <form className="form-stack" onSubmit={(event) => void submit(event)}>
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
        <label className="merge-permanence"><input type="checkbox" checked={permanent} disabled={dispatched || stale} onChange={(event) => setPermanent(event.target.checked)} /><span>I understand this permanently makes the source immutable and cannot be undone.</span></label>
        {stale && selected && <div className="error-notice" role="alert"><p>The reviewed source or destination changed. Refetch both contexts to create a new merge operation.</p><button type="button" className="button button-secondary" disabled={reviewing} onClick={() => void loadReview(selected)}>Refetch both contexts</button></div>}
        {reviewError && !stale && <div className="error-notice" role="alert"><p>{reviewError}</p></div>}
        <div className="dialog-actions">
          <button type="button" className="button button-secondary" disabled={cannotClose} onClick={onClose}>Cancel</button>
          <button type="submit" className="button button-danger" disabled={saving || dispatched || stale || blocked || !permanent || !rationale.trim()}>{saving ? "Merging…" : "Permanently merge source"}</button>
        </div>
      </form>}
      {reviewError && !review && <div className="error-notice" role="alert"><p>{reviewError}</p></div>}
    </div>
  </dialog>;
}
