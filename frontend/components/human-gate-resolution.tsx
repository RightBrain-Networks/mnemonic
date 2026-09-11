"use client";

import { useEffect, useState, type FormEvent } from "react";
import { ApiError, errorMessage } from "@/lib/api";
import { dashboardSessionId } from "@/lib/dashboard-session";
import { humanGatePath, humanGateProjectionKey } from "@/lib/human-gates";
import { mutationGateKey, mutationWorkKey, useMutationIntentRegistry, useMutationScope } from "@/lib/mutation-intent";
import type { HumanGateRead, HumanGateResolutionInput } from "@/lib/types";

export default function HumanGateResolution({ gate, disabled = false, onResolved, onRefresh }: {
  gate: HumanGateRead;
  disabled?: boolean;
  onResolved: () => void | Promise<void>;
  onRefresh?: () => void | Promise<void>;
}) {
  const registry = useMutationIntentRegistry();
  const [answer, setAnswer] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [staleKey, setStaleKey] = useState<string | null>(null);
  const [message, setMessage] = useState<{ text: string; error?: boolean } | null>(null);
  const gateKey = mutationGateKey(gate.project_id, gate.id);
  const workKey = mutationWorkKey(gate.project_id, gate.work_item_id);
  const gateScope = useMutationScope({ conflictKeys: [gateKey] }, registry);
  const pendingIntent = gateScope.intents[0];
  const projectionKey = humanGateProjectionKey(gate);
  const needsRefresh = staleKey === projectionKey;

  useEffect(() => {
    setAnswer("");
    setMessage(null);
    setStaleKey(null);
  }, [gate.id]);

  useEffect(() => registry.subscribeRecovered((intent) => {
    if (intent.kind !== "resolve_human_input" || !intent.conflictKeys.includes(gateKey)) return;
    setAnswer("");
    setMessage({ text: "Answer recovered. Refreshing the queue." });
    void onResolved();
  }), [gateKey, onResolved, registry]);

  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (!answer.trim() || submitting || pendingIntent || disabled || needsRefresh) return;
    const payload: HumanGateResolutionInput = {
      resolution: answer,
      resolved_by_client: "dashboard",
      resolved_by_session_id: dashboardSessionId(),
      resolved_by_model: null,
      expected_question_version: gate.question_version,
      reviewed_context_revision: gate.current_context_revision
    };
    setSubmitting(true);
    setMessage(null);
    try {
      await registry.execute({
        kind: "resolve_human_input", slot: `gate-resolution:${gate.id}`,
        projectId: gate.project_id, conflictKeys: [workKey, gateKey], method: "POST",
        path: `${humanGatePath(gate.project_id, gate.work_item_id, gate.id)}/resolve`, payload
      });
      setAnswer("");
      setMessage({ text: "Answer recorded." });
      await onResolved();
    } catch (cause) {
      if (cause instanceof ApiError && ["gate_context_changed", "gate_question_changed"].includes(cause.code ?? "")) {
        setStaleKey(projectionKey);
        setMessage({ text: "This question or its work changed. Your draft is saved here; read the refreshed question before sending it.", error: true });
        void onRefresh?.();
      } else {
        setMessage({ text: errorMessage(cause), error: true });
      }
    } finally {
      setSubmitting(false);
    }
  }

  return <div className="gate-resolution">
    <form onSubmit={(event) => void submit(event)}>
      <label className="field">Your answer
        <textarea rows={5} maxLength={4_000} value={answer}
          disabled={Boolean(pendingIntent) || disabled}
          onChange={(event) => setAnswer(event.target.value)}
          placeholder="Record your decision, constraints, or guidance…" />
        <span className="field-hint">{Array.from(answer).length}/4,000 · Saved with this question.</span>
      </label>
      {pendingIntent && <div className="gate-pending-intent" role="status">Your answer has an unresolved outcome. Use the pending-mutation recovery control.</div>}
      {message && <div className={message.error ? "error-notice" : "gate-success"} role={message.error ? "alert" : "status"}><p>{message.text}</p></div>}
      {needsRefresh && onRefresh && <button type="button" className="button button-secondary" onClick={() => void onRefresh()}>Refresh question</button>}
      <button type="submit" className="button button-primary"
        disabled={submitting || Boolean(pendingIntent) || !answer.trim() || disabled || needsRefresh}
      >{submitting ? "Recording answer…" : "Record answer"}</button>
    </form>
  </div>;
}
