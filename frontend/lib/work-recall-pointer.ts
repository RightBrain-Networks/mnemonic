import type { WorkSummary } from "@/lib/types";

export function workRecallPointer(summary: WorkSummary): string {
  const work = summary.work_item;
  return `Recall the Mnemonic work item "${work.title}" (project_id ${work.project_id}, work_item_id ${work.id}) using recall_work, then summarise its current context and wait for my direction.`;
}
