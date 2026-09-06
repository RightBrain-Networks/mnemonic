import type {
  JobCompletionReportInput,
  LeasePublic,
  LeaseReleaseResult,
  MutationActor,
  Readiness,
  WorkItem,
  WorkStatus
} from "./types.ts";
import { decodeLeasePublic } from "./readiness-codecs.ts";
import { exactKeys, objectValue, sameUuid } from "./wire-guards.ts";

export type ManualStatusAction =
  | "defer"
  | "pending"
  | "active"
  | "done"
  | "wont-do"
  | "promoted";
export type AlternateStatusAction = Exclude<ManualStatusAction, "defer">;

export const alternateStatusActions = [
  { value: "pending", label: "Pending" },
  { value: "active", label: "Active" },
  { value: "done", label: "Done" },
  { value: "wont-do", label: "Won’t Do" },
  { value: "promoted", label: "Promote" }
] as const satisfies readonly {
  value: AlternateStatusAction;
  label: string;
}[];

export function currentManualStatusAction(
  status: WorkStatus,
  readiness: Readiness
): ManualStatusAction | null {
  if (readiness.has_active_lease) return "active";
  if (readiness.has_dropped_lease) return null;
  return status === "deferred" ? "defer" : status;
}

export function availableStatusActions(
  status: WorkStatus,
  readiness: Readiness
): typeof alternateStatusActions[number][] {
  const current = currentManualStatusAction(status, readiness);
  return alternateStatusActions.filter((action) => action.value !== current);
}

export function statusActionDisabledReason(
  action: AlternateStatusAction,
  readiness: Readiness,
  reportSettingsReady: boolean
): string | null {
  if (action === "active" && readiness.is_gated) {
    return "Resolve every human question before marking this work Active.";
  }
  if (action === "active" && readiness.is_blocked) {
    return "Resolve every incoming blocker before marking this work Active.";
  }
  if (action === "done" && readiness.is_blocked) {
    return "Resolve every incoming blocker before marking this work Done.";
  }
  if (["done", "wont-do", "promoted"].includes(action) && readiness.is_gated) {
    return "Resolve every human question before making a terminal status decision.";
  }
  if (["done", "wont-do", "promoted"].includes(action) && !reportSettingsReady) {
    return "Wait for the project’s human-report settings before making this decision.";
  }
  return null;
}

export function humanDecisionReport(
  work: WorkItem,
  status: "done" | "wont-do" | "promoted",
  promptRevision: string
): JobCompletionReportInput {
  const decision = status === "done"
    ? "Done"
    : status === "wont-do"
      ? "Won’t Do"
      : "Promoted";
  const consequence = status === "done"
    ? "No additional implementation or verification evidence was supplied with this action."
    : status === "wont-do"
      ? "No further implementation is planned on this work item."
      : "No external destination is inferred or created by this action.";
  return {
    summary: `A person explicitly marked “${work.title}” ${decision} in the Mnemonic dashboard. ${consequence}`,
    fyi_items: [],
    prompt_revision: promptRevision
  };
}

export function humanDecisionCompletionCheckpoint(work: WorkItem): string {
  return (
    `Explicit human decision from the Mnemonic dashboard: “${work.title}” was manually `
    + "marked Done. This checkpoint records the status decision only and makes no "
    + "additional implementation or verification claim."
  );
}

export function decodeDashboardActivationResult(
  value: unknown,
  actor: MutationActor
): LeasePublic {
  let lease: LeasePublic;
  try {
    lease = decodeLeasePublic(value);
  } catch {
    throw new Error("Mnemonic returned an invalid manual activation.");
  }
  if (
    lease.holder_client !== actor.actor_client
    || lease.holder_session_id !== actor.actor_session_id
  ) throw new Error("Mnemonic returned an invalid manual activation.");
  return lease;
}

export function decodeLeaseReleaseResult(
  value: unknown,
  workItemId: string
): LeaseReleaseResult {
  const result = objectValue(value);
  if (
    !result || !exactKeys(result, ["work_item_id", "released"])
    || !sameUuid(result.work_item_id, workItemId) || typeof result.released !== "boolean"
  ) throw new Error("Mnemonic returned an invalid manual Pending result.");
  return result as unknown as LeaseReleaseResult;
}
