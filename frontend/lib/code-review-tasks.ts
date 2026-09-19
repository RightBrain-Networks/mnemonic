import type { CodeReviewDetail, HumanReviewDecision } from "./code-reviews.ts";
import type { Task } from "./tasks.ts";
import type { ManualStatusAction } from "./work-status-actions.ts";

export const reviewStatusFilters = ["pending", "active", "deferred", "done", "wont-do", "promoted", "superseded", "all"] as const;

export function reviewStatusActions(task: Task) {
  return ([
    { value: "to-review", label: "Pending", status: "pending" },
    { value: "done", label: "Done", status: "done" },
    { value: "wont-do", label: "Won’t Do", status: "wont-do" },
    { value: "promoted", label: "Promote", status: "promoted" }
  ] as const).filter((action) => action.status !== task.status && !(action.status === "pending" && task.status === "active"));
}

export function reviewDecisionStatus(action: ManualStatusAction): HumanReviewDecision["status"] {
  if (action === "defer") return "deferred";
  if (action === "pending" || action === "to-review") return "to-review";
  if (action === "review") throw new Error("Select an action for the existing review.");
  return action;
}

export function retainedReviewPointer(detail: CodeReviewDetail): string {
  const review = detail.review;
  return `Recall this saved code review with get_code_review using the exact routing below. Read its retained context and report its current state; this pointer does not authorize execution or reopening the review.\n\n${JSON.stringify({
    project_id: review.project_id, work_item_id: review.work_item_id, code_review_id: review.id
  }, null, 2)}`;
}
