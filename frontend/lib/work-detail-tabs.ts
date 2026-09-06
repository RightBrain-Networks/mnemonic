import type { WorkContext, WorkSummary } from "@/lib/types";

export type DetailTab =
  | "context"
  | "history"
  | "evidence"
  | "graph"
  | "questions"
  | "reviews"
  | "activity";

export const DETAIL_TABS: readonly DetailTab[] = [
  "context",
  "history",
  "evidence",
  "graph",
  "questions",
  "reviews",
  "activity"
];

export const detailTabLabels: Record<DetailTab, string> = {
  context: "Context",
  history: "History",
  evidence: "Evidence",
  graph: "Graph",
  questions: "Questions",
  reviews: "Code review",
  activity: "Activity"
};

export interface DetailTabDescriptor {
  key: DetailTab;
  label: string;
  // Omitted (not undefined) when the count is unknown until the context loads.
  count?: number;
  alert: boolean;
}

function describe(key: DetailTab, count: number | undefined, alert = false): DetailTabDescriptor {
  return {
    key,
    label: detailTabLabels[key],
    ...(count === undefined ? {} : { count }),
    alert
  };
}

// Counts fall back to the queue summary while the bounded context is still
// loading so the tab bar renders with the selection instead of after it.
export function detailTabs(
  context: WorkContext | null,
  summary: WorkSummary
): DetailTabDescriptor[] {
  const questions = context?.unresolved_gate_total ?? summary.readiness.unresolved_gate_count;
  return [
    describe("context", undefined),
    describe("history", context?.checkpoint_total ?? summary.checkpoint_count),
    describe("evidence", undefined),
    describe("graph", context?.relationship_counts.total),
    describe("questions", questions, questions > 0),
    describe("reviews", undefined, Boolean(context?.code_review_context?.current_review || context?.code_review_context?.pending_follow_up)),
    describe("activity", context?.event_total)
  ];
}

export type CopyKind = "id" | "pointer" | "context" | "cold-review" | "audit-id" | "canonical-id";

export function copyKey(id: string, kind: CopyKind): string {
  return `${id}:${kind}`;
}
