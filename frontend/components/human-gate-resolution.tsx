"use client";

import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { ApiError, api, errorMessage } from "@/lib/api";
import { currentContext } from "@/lib/current-context";
import { dashboardSessionId } from "@/lib/dashboard-session";
import {
  decodeHumanGate,
  hasCompleteRelationshipReview,
  humanGateChangedLabels,
  humanGatePath,
  sameHumanGateRevision
} from "@/lib/human-gates";
import {
  mutationGateKey,
  mutationWorkKey,
  useMutationIntentRegistry,
  useMutationIntents
} from "@/lib/mutation-intent";
import type {
  HumanGateRead,
  HumanGateResolutionInput,
  WorkContext
} from "@/lib/types";

function gateFromContext(context: WorkContext, gate: HumanGateRead): HumanGateRead {
  const value = context.unresolved_gates.find((candidate) => (
    candidate.id.toLowerCase() === gate.id.toLowerCase()
  ));
  if (!value) {
    throw new Error("The reviewed work context did not include this unresolved question.");
  }
  const decoded = decodeHumanGate(value, {
    projectId: gate.project_id,
    workItemId: gate.work_item_id,
    gateId: gate.id,
    status: "unresolved"
  });
  const checkpoint = currentContext(context);
  if (
    context.work_item.project_id.toLowerCase() !== gate.project_id.toLowerCase()
    || context.work_item.id.toLowerCase() !== gate.work_item_id.toLowerCase()
    || context.work_item.version !== decoded.current_context_revision.work_version
    || checkpoint.id.toLowerCase()
      !== decoded.current_context_revision.context_checkpoint_id.toLowerCase()
  ) {
    throw new Error("Mnemonic returned an incoherent human-gate review bundle.");
  }
  return decoded;
}

function gateProjectionKey(gate: HumanGateRead): string {
  return [
    gate.current_context_revision.work_version,
    gate.current_context_revision.context_checkpoint_id.toLowerCase(),
    gate.current_context_revision.relationship_event_count,
    gate.context_changed_since_request
  ].join(":");
}

export default function HumanGateResolution({
  gate,
  reviewedContext: suppliedContext,
  onResolved
}: {
  gate: HumanGateRead;
  reviewedContext?: WorkContext;
  onResolved: () => void | Promise<void>;
}) {
  const registry = useMutationIntentRegistry();
  const intents = useMutationIntents(registry);
  const initialReviewContext = suppliedContext
    && hasCompleteRelationshipReview(suppliedContext) ? suppliedContext : null;
  const [answer, setAnswer] = useState("");
  const [reviewContext, setReviewContext] = useState<WorkContext | null>(initialReviewContext);
  const [reviewedGate, setReviewedGate] = useState<HumanGateRead | null>(() => (
    initialReviewContext ? gateFromContext(initialReviewContext, gate) : null
  ));
  const [acknowledged, setAcknowledged] = useState(false);
  const [loadingReview, setLoadingReview] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [message, setMessage] = useState<{ text: string; error?: boolean } | null>(null);
  const loadedReviewRevisionKey = useRef<string | null>(null);
  const loadedReviewAgainstIncomingKey = useRef<string | null>(null);
  const gateKey = mutationGateKey(gate.project_id, gate.id);
  const workKey = mutationWorkKey(gate.project_id, gate.work_item_id);
  const pendingIntent = intents.find((intent) => (
    intent.state !== "prepared" && intent.conflictKeys.includes(gateKey)
  ));
  const incomingRevisionKey = gateProjectionKey(gate);
  const reviewedProjectionIsCurrent = Boolean(
    reviewContext
    && reviewedGate
    && reviewedGate.id.toLowerCase() === gate.id.toLowerCase()
    && reviewedGate.project_id.toLowerCase() === gate.project_id.toLowerCase()
    && reviewedGate.work_item_id.toLowerCase() === gate.work_item_id.toLowerCase()
    && (
      (
        sameHumanGateRevision(
          reviewedGate.current_context_revision,
          gate.current_context_revision
        )
        && reviewedGate.context_changed_since_request === gate.context_changed_since_request
      )
      || loadedReviewAgainstIncomingKey.current === incomingRevisionKey
    )
  );
  const effectiveGate = reviewedProjectionIsCurrent ? reviewedGate! : gate;
  const effectiveReviewContext = reviewedProjectionIsCurrent ? reviewContext : null;
  const relationshipReviewComplete = Boolean(
    effectiveReviewContext && hasCompleteRelationshipReview(effectiveReviewContext)
  );
  const changedLabels = humanGateChangedLabels(effectiveGate);
  const needsReview = effectiveGate.context_changed_since_request;
  const reviewArmed = !needsReview || Boolean(
    effectiveReviewContext && reviewedGate && relationshipReviewComplete && acknowledged
  );

  useEffect(() => {
    loadedReviewRevisionKey.current = null;
    loadedReviewAgainstIncomingKey.current = null;
    setAnswer("");
    setAcknowledged(false);
    setMessage(null);
    setReviewContext(null);
    setReviewedGate(null);
  }, [gate.id]);

  useEffect(() => {
    if (!suppliedContext) return;
    if (!hasCompleteRelationshipReview(suppliedContext)) {
      loadedReviewRevisionKey.current = null;
      loadedReviewAgainstIncomingKey.current = null;
      setReviewContext(null);
      setReviewedGate(null);
      setAcknowledged(false);
      return;
    }
    const projected = gateFromContext(suppliedContext, gate);
    loadedReviewRevisionKey.current = gateProjectionKey(projected);
    loadedReviewAgainstIncomingKey.current = incomingRevisionKey;
    setReviewContext(suppliedContext);
    setReviewedGate(projected);
    setAcknowledged(false);
  }, [gate.id, suppliedContext]);

  useEffect(() => {
    if (suppliedContext) return;
    if (loadedReviewRevisionKey.current === incomingRevisionKey) return;
    loadedReviewRevisionKey.current = null;
    loadedReviewAgainstIncomingKey.current = null;
    setReviewContext(null);
    setReviewedGate(null);
    setAcknowledged(false);
  }, [incomingRevisionKey, suppliedContext]);

  useEffect(() => registry.subscribeRecovered((intent) => {
    if (intent.kind !== "resolve_human_input" || !intent.conflictKeys.includes(gateKey)) return;
    setAnswer("");
    setAcknowledged(false);
    setMessage({ text: "The original answer was recovered exactly. Current views are refreshing." });
    void onResolved();
  }), [gateKey, onResolved, registry]);

  async function loadReview(): Promise<boolean> {
    setLoadingReview(true);
    setMessage(null);
    try {
      const params = new URLSearchParams({
        recent_limit: "5",
        recent_event_limit: "10"
      });
      const context = await api<WorkContext>(
        `${humanGatePath(gate.project_id, gate.work_item_id, gate.id)}/context?${params}`
      );
      const projected = gateFromContext(context, gate);
      if (!hasCompleteRelationshipReview(context)) {
        throw new Error(
          "Mnemonic returned an incomplete relationship review. Reload before acknowledging drift."
        );
      }
      loadedReviewRevisionKey.current = gateProjectionKey(projected);
      loadedReviewAgainstIncomingKey.current = incomingRevisionKey;
      setReviewContext(context);
      setReviewedGate(projected);
      setAcknowledged(false);
      if (!projected.context_changed_since_request) {
        setMessage({ text: "Current context now matches the request anchor; no drift acknowledgement is needed." });
      }
      return true;
    } catch (cause) {
      loadedReviewRevisionKey.current = null;
      loadedReviewAgainstIncomingKey.current = null;
      setReviewContext(null);
      setReviewedGate(null);
      setAcknowledged(false);
      setMessage({ text: errorMessage(cause), error: true });
      return false;
    } finally {
      setLoadingReview(false);
    }
  }

  const reviewedCheckpoint = useMemo(() => (
    effectiveReviewContext ? currentContext(effectiveReviewContext) : null
  ), [effectiveReviewContext]);
  const reviewedRelationships = useMemo(() => effectiveReviewContext ? [
    ...effectiveReviewContext.incoming_relationships,
    ...effectiveReviewContext.outgoing_relationships,
    ...effectiveReviewContext.undirected_relationships
  ] : [], [effectiveReviewContext]);

  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (!answer.trim() || submitting || pendingIntent || !reviewArmed) return;
    const source = effectiveGate;
    const acknowledgeContextChange = source.context_changed_since_request;
    const payload: HumanGateResolutionInput = {
      resolution: answer,
      resolved_by_client: "dashboard",
      resolved_by_session_id: dashboardSessionId(),
      resolved_by_model: null,
      acknowledge_context_change: acknowledgeContextChange,
      ...(acknowledgeContextChange
        ? { reviewed_context_revision: source.current_context_revision }
        : {})
    };
    setSubmitting(true);
    setMessage(null);
    try {
      await registry.execute({
        kind: "resolve_human_input",
        slot: `gate-resolution:${gate.id}`,
        projectId: gate.project_id,
        conflictKeys: [workKey, gateKey],
        method: "POST",
        path: `${humanGatePath(gate.project_id, gate.work_item_id, gate.id)}/resolve`,
        payload
      });
      setAnswer("");
      setAcknowledged(false);
      setMessage({ text: "Answer recorded. No other action was executed." });
      await onResolved();
    } catch (cause) {
      if (cause instanceof ApiError && cause.code === "gate_context_changed") {
        loadedReviewRevisionKey.current = null;
        loadedReviewAgainstIncomingKey.current = null;
        setReviewContext(null);
        setReviewedGate(null);
        setAcknowledged(false);
        setMessage({
          text: "The work changed after your review. Your answer is still here; review the new current context before submitting a new intent.",
          error: true
        });
        const reloaded = await loadReview();
        setMessage({
          text: reloaded
            ? "The work changed after your review. Your answer is still here; review and acknowledge the newly loaded context before submitting a new intent."
            : "The work changed after your review. Your answer is still here, but the new current context could not be loaded. Retry the review before submitting.",
          error: true
        });
      } else {
        setMessage({ text: errorMessage(cause), error: true });
      }
    } finally {
      setSubmitting(false);
    }
  }

  return <div className="gate-resolution">
    {needsReview && <section className="gate-drift" aria-label="Context changed since request">
      <strong>Context changed after this question was requested.</strong>
      <span>Changed: {changedLabels.join(", ")}.</span>
      {!effectiveReviewContext && <button
        type="button"
        className="button button-secondary"
        disabled={loadingReview || Boolean(pendingIntent)}
        onClick={() => void loadReview()}
      >{loadingReview ? "Loading current context…" : "Review current context"}</button>}
      {effectiveReviewContext && reviewedCheckpoint && <div className="gate-context-review">
        <div><span className="section-label">EXACT REVIEW BUNDLE</span><strong>{effectiveReviewContext.work_item.title}</strong><span>Version {effectiveReviewContext.work_item.version} · {effectiveReviewContext.relationship_counts.total} current relationships</span></div>
        <p>{effectiveReviewContext.work_item.summary}</p>
        <pre tabIndex={0}>{reviewedCheckpoint.prompt}</pre>
        <dl className="gate-review-revision">
          <div><dt>Work version</dt><dd>{effectiveGate.current_context_revision.work_version}</dd></div>
          <div><dt>Context checkpoint</dt><dd className="mono break-all">{effectiveGate.current_context_revision.context_checkpoint_id}</dd></div>
          <div><dt>Relationship revision</dt><dd>{effectiveGate.current_context_revision.relationship_event_count}</dd></div>
        </dl>
        <details className="gate-review-relationships">
          <summary>Review current relationships ({effectiveReviewContext.relationship_counts.total})</summary>
          {reviewedRelationships.length > 0 ? <ul>{reviewedRelationships.map((relationship) => <li key={relationship.relationship.id}>
            <strong>{relationship.relationship.relationship_type}</strong>
            <span>{relationship.direction} · {relationship.counterpart.title}</span>
          </li>)}</ul> : <p>No current relationships.</p>}
          {!relationshipReviewComplete && <p role="alert">The relationship review is incomplete. Reload the authoritative review bundle before acknowledging drift.</p>}
        </details>
        <label className="gate-acknowledgement">
          <input
            type="checkbox"
            checked={acknowledged}
            disabled={Boolean(pendingIntent) || !relationshipReviewComplete}
            onChange={(event) => setAcknowledged(event.target.checked)}
          />
          <span>I reviewed this exact current work, context checkpoint, and relationship state.</span>
        </label>
        <button type="button" className="text-link" disabled={loadingReview || Boolean(pendingIntent)} onClick={() => void loadReview()}>Reload review bundle</button>
      </div>}
    </section>}
    <form onSubmit={(event) => void submit(event)}>
      <label className="field">Durable answer
        <textarea
          rows={5}
          maxLength={4_000}
          value={answer}
          disabled={Boolean(pendingIntent)}
          onChange={(event) => setAnswer(event.target.value)}
          placeholder="Record the human decision, constraints, or next-step guidance…"
        />
        <span className="field-hint"><span>{Array.from(answer).length}/4,000</span> Stored exactly and cannot be edited or deleted. Do not include credentials or private chain-of-thought.</span>
      </label>
      <p className="gate-authority-warning">Resolving records this answer as durable context. It does not execute, approve, or authorize another action.</p>
      {pendingIntent && <div className="gate-pending-intent" role="status">This exact answer has an unresolved outcome. Use the pending-mutation recovery control; changing or resubmitting it is blocked.</div>}
      {message && <div className={message.error ? "error-notice" : "gate-success"} role={message.error ? "alert" : "status"}><p>{message.text}</p></div>}
      <button
        type="submit"
        className="button button-primary"
        disabled={submitting || Boolean(pendingIntent) || !answer.trim() || !reviewArmed}
      >{submitting ? "Recording answer…" : "Record answer"}</button>
    </form>
  </div>;
}
