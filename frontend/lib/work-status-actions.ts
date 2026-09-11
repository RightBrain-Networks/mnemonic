import type {
  JobCompletionReportInput,
  LeaseReleaseResult,
  Readiness,
  WorkItem,
  WorkStatus
} from "./types.ts";
import { exactKeys, objectValue, sameUuid } from "./wire-guards.ts";

export type ManualStatusAction =
  | "defer"
  | "pending"
  | "to-review"
  | "done"
  | "wont-do"
  | "promoted";
export type AlternateStatusAction = Exclude<ManualStatusAction, "defer">;

export const alternateStatusActions = [
  { value: "pending", label: "Pending" },
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
): ManualStatusAction | "active" | null {
  const review = readiness.review_status ?? (readiness.display_state === "to-review" ? "to-review" : null);
  if (review) return review === "deferred" ? "defer" : review;
  if (status === "done") return "done";
  if (readiness.has_active_lease) return "active";
  if (readiness.has_dropped_lease) return null;
  return status === "deferred" ? "defer" : status;
}

export function availableStatusActions(
  status: WorkStatus,
  readiness: Readiness
): { value: AlternateStatusAction; label: string }[] {
  const current = currentManualStatusAction(status, readiness);
  const review = readiness.review_status || readiness.display_state === "to-review";
  const actions = alternateStatusActions.map((action) => review && action.value === "pending"
    ? { value: "to-review" as const, label: "To review" } : action);
  return actions.filter((action) => action.value !== current);
}

export function statusActionDisabledReason(
  action: AlternateStatusAction,
  readiness: Readiness,
  reportSettingsReady: boolean
): string | null {
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
