"use client";

import {
  selectMutationScope,
  type DispatchedMutationIntent,
  type MutationIntentSummary
} from "@/lib/mutation-intent";
import { mutationLabels } from "@/lib/mutation-recovery";

export type MutationRecoveryNotice = {
  titles: Record<DispatchedMutationIntent["state"], string>;
  description: string;
  retryLabel: string;
};

function RetryButton({
  intent,
  retryingMutation,
  onRetry,
  label = "Retry exact request"
}: {
  intent: DispatchedMutationIntent;
  retryingMutation: string;
  onRetry: (intent: MutationIntentSummary) => void;
  label?: string;
}) {
  if (intent.state !== "unresolved") return null;
  return <button
    type="button"
    className="button button-secondary"
    disabled={Boolean(retryingMutation)}
    onClick={() => onRetry(intent)}
  >{retryingMutation === intent.slot ? "Retrying…" : label}</button>;
}

export default function MutationRecoveryPanel({
  intents,
  retryingMutation,
  onRetry,
  modal = false,
  notice
}: {
  intents: readonly MutationIntentSummary[];
  retryingMutation: string;
  onRetry: (intent: MutationIntentSummary) => void;
  modal?: boolean;
  notice?: MutationRecoveryNotice;
}) {
  const pending = selectMutationScope(intents).intents;
  if (!pending.length) return null;
  if (notice && pending.length === 1) {
    const intent = pending[0];
    return <div className="mutation-recovery" role="alert" aria-live="polite">
      <strong>{notice.titles[intent.state]}</strong>
      <span>{notice.description}</span>
      <RetryButton intent={intent} retryingMutation={retryingMutation} onRetry={onRetry} label={notice.retryLabel} />
    </div>;
  }
  return <section
    className={`mutation-recovery ${modal ? "mutation-recovery-modal" : "mutation-recovery-global"}`}
    role="alert"
    aria-live="polite"
  >
    <div>
      <strong>Pending mutations need this tab.</strong>
      <span>Do not reload or close it; the exact retry request exists only in memory.</span>
    </div>
    <ul>{pending.map((intent) => <li key={intent.slot}>
      <span>{mutationLabels[intent.kind]} · {intent.state === "in_flight"
        ? "waiting for a response"
        : intent.state === "safety_conflict"
          ? "safety conflict"
          : "outcome unknown"}</span>
      {intent.state === "safety_conflict"
        && <small>Stop and inspect the client and server state before continuing.</small>}
      <RetryButton intent={intent} retryingMutation={retryingMutation} onRetry={onRetry} />
    </li>)}</ul>
  </section>;
}
