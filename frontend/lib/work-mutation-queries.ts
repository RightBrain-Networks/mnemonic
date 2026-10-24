import type { QueryClient } from "@tanstack/react-query";
import type { ConfirmedMutation, MutationIntentRegistry } from "./mutation-intent.ts";
import type { MutationKind } from "./mutation-responses.ts";

type ReadRoot = "work" | "tasks" | "work-detail" | "attention-count" | "attention" | "report-count" | "reports" | "knowledge";
const workReads = ["work", "tasks", "work-detail", "attention-count", "attention"] as const;
const closeoutReads = [...workReads, "report-count", "reports"] as const;

// Keep this exhaustive: every protected write must declare its read dependencies.
const mutationReadRoots = {
  decision_change_update: [...workReads, "knowledge"],
  create_work: workReads,
  add_checkpoint: workReads,
  append_event: workReads,
  add_relationship: workReads,
  update_work: closeoutReads,
  defer_work: workReads,
  complete_work: closeoutReads,
  respond_to_work_follow_up: workReads,
  delete_work: closeoutReads,
  move_work: closeoutReads,
  remove_relationship: workReads,
  resolve_human_input: [...workReads, "knowledge"],
  merge_work: closeoutReads,
  dismiss_job_completion_report: ["work-detail", "report-count", "reports"],
  create_job_completion_report_follow_up: closeoutReads
} satisfies Record<MutationKind, readonly ReadRoot[]>;

function affectedProjects(confirmation: ConfirmedMutation): Set<string> {
  const projects = new Set<string>([confirmation.intent.projectId.toLowerCase()]);
  const addProject = (projectId: string) => projects.add(projectId.toLowerCase());
  // A relationship's authority project need not contain both endpoints. Moves
  // likewise freeze the source and target project identities before dispatch.
  for (const key of confirmation.intent.conflictKeys) {
    const match = /^(?:project|work|gate|report|work-create):([^:]+)(?::|$)/.exec(key);
    if (match) addProject(match[1]!);
  }
  switch (confirmation.kind) {
    case "create_work":
      addProject(confirmation.result.work_item.project_id);
      for (const edge of confirmation.result.initial_relationships) addProject(edge.project_id);
      break;
    case "complete_work":
    case "create_job_completion_report_follow_up":
      addProject(confirmation.result.work_item.project_id);
      break;
    case "decision_change_update":
    case "update_work":
    case "defer_work":
    case "append_event":
    case "delete_work":
    case "remove_relationship":
    case "resolve_human_input":
    case "dismiss_job_completion_report":
      addProject(confirmation.result.project_id);
      break;
    case "respond_to_work_follow_up":
      addProject(confirmation.result.follow_up.project_id);
      break;
    case "add_relationship":
      addProject(confirmation.result.relationship.project_id);
      break;
    case "move_work":
      addProject(confirmation.result.source_project_id);
      addProject(confirmation.result.target_project_id);
      addProject(confirmation.result.work_item.project_id);
      break;
    case "merge_work":
      addProject(confirmation.result.source_work_item.project_id);
      addProject(confirmation.result.destination_work_item.project_id);
      break;
    case "add_checkpoint":
      break;
  }
  return projects;
}

/** Only callers holding a strictly decoded mutation success may reconcile reads. */
export function invalidateConfirmedWorkMutation(client: QueryClient, confirmation: ConfirmedMutation) {
  const projects = affectedProjects(confirmation);
  const roots = new Set<string>(mutationReadRoots[confirmation.kind]);
  // Details include relationship pointers and derived readiness, so a changed
  // work item can invalidate cached details elsewhere in the affected project.
  return client.invalidateQueries({
    predicate: ({ queryKey }) => queryKey[0] === "mnemonic"
      && queryKey[1] === "project"
      && (projects.has(String(queryKey[2]).toLowerCase())
        || queryKey[3] === "work-detail" && queryKey[5] === "report"
          && projects.has(String(queryKey[6]).toLowerCase()))
      && roots.has(String(queryKey[3])),
    refetchType: "active"
  });
}

/** Writes stay owned by MutationIntentRegistry; Query only refreshes safe reads. */
export function connectWorkMutationQueries(client: QueryClient, registry: MutationIntentRegistry): () => void {
  return registry.subscribeConfirmed((confirmation) => {
    void invalidateConfirmedWorkMutation(client, confirmation);
  });
}
