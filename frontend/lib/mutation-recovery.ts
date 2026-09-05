import {
  matchesMutationScope,
  selectMutationScope,
  type DispatchedMutationIntent,
  type MutationIntentSummary,
  type MutationScope
} from "./mutation-intent.ts";

export const mutationLabels: Record<MutationIntentSummary["kind"], string> = {
  create_work: "Create work",
  add_checkpoint: "Add checkpoint",
  append_event: "Append progress",
  add_relationship: "Add relationship",
  update_work: "Update work",
  defer_work: "Defer work",
  complete_work: "Complete work",
  delete_work: "Delete work",
  remove_relationship: "Remove relationship",
  resolve_human_input: "Resolve human question",
  merge_work: "Merge duplicate work",
  dismiss_job_completion_report: "Dismiss summary",
  create_job_completion_report_follow_up: "Create report follow-up"
};

type RecoveryOwner = "createDialog" | "deleteDialog" | "mergePanel" | "openedPane";

export function selectMutationRecovery(
  intents: Iterable<MutationIntentSummary>,
  targets: {
    createWorkKey?: string;
    deleteWorkKey?: string;
    mergeSlot?: string;
    openedWorkKey?: string;
  }
): Record<RecoveryOwner | "global", readonly DispatchedMutationIntent[]> {
  const groups: Record<RecoveryOwner | "global", DispatchedMutationIntent[]> = {
    createDialog: [], deleteDialog: [], mergePanel: [], openedPane: [], global: []
  };
  const owners: Array<{ owner: RecoveryOwner; scope: MutationScope | null }> = [
    { owner: "createDialog", scope: targets.createWorkKey ? {
      kinds: ["create_work"], conflictKeys: [targets.createWorkKey]
    } : null },
    { owner: "deleteDialog", scope: targets.deleteWorkKey ? {
      kinds: ["delete_work"], conflictKeys: [targets.deleteWorkKey]
    } : null },
    { owner: "mergePanel", scope: targets.mergeSlot ? {
      kinds: ["merge_work"], slot: targets.mergeSlot
    } : null },
    { owner: "openedPane", scope: targets.openedWorkKey ? {
      conflictKeys: [targets.openedWorkKey]
    } : null }
  ];
  for (const intent of selectMutationScope(intents).intents) {
    const target = owners.find(({ scope }) => scope && matchesMutationScope(intent, scope));
    groups[target?.owner ?? "global"].push(intent);
  }
  return groups;
}
