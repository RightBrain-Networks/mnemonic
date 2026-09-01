import type { Project, WorkSummary } from "@/lib/types";

export const DEFAULT_RECALL_POINTER_TEMPLATE =
  `Recall the mnemonic work item "$WORK_ITEM_TITLE" (project_id $PROJECT_ID, work_item_id $WORK_ITEM_ID) using \`recall_work\`. Verify its premises and, if confirmed, proceed with the work as described.

If the stated premises are refuted or you determine that no work is needed, close the issue as "won't do" with a detailed disposition explanation. If you acquire a work lease, create a background task to remind you to renew it prior to expiration. Reset the timer upon work release renewal.`;

export const RECALL_POINTER_MACROS = [
  { macro: "$WORK_ITEM_TITLE", description: "The work item's title." },
  { macro: "$WORK_ITEM_SUMMARY", description: "The work item's summary." },
  { macro: "$WORK_ITEM_STATUS", description: "The work item's lifecycle status." },
  { macro: "$WORK_ITEM_PRIORITY", description: "The work item's numeric priority." },
  { macro: "$PROJECT_ID", description: "The project's unique ID." },
  { macro: "$PROJECT_NAME", description: "The project's name." },
  { macro: "$PROJECT_SLUG", description: "The project's URL-safe slug." },
  { macro: "$WORK_ITEM_ID", description: "The work item's unique ID." }
] as const;

export type RecallPointerMacro = typeof RECALL_POINTER_MACROS[number]["macro"];
export type RecallPointerValues = Record<RecallPointerMacro, string>;

export type WorkRecallPointerOptions = {
  template?: string;
  project?: Pick<Project, "name" | "slug">;
};

const MACRO_PATTERN = /\$[A-Za-z_][A-Za-z0-9_]*/g;

export function expandRecallPointerTemplate(
  template: string,
  values: Readonly<RecallPointerValues>
): string {
  return template.replace(MACRO_PATTERN, (macro) =>
    values[macro as RecallPointerMacro] ?? macro
  );
}

export function workRecallPointer(
  summary: WorkSummary,
  options: WorkRecallPointerOptions = {}
): string {
  const work = summary.work_item;
  return expandRecallPointerTemplate(
    options.template ?? DEFAULT_RECALL_POINTER_TEMPLATE,
    {
      $WORK_ITEM_TITLE: work.title,
      $WORK_ITEM_SUMMARY: work.summary,
      $WORK_ITEM_STATUS: work.status,
      $WORK_ITEM_PRIORITY: String(work.priority),
      $PROJECT_ID: work.project_id,
      $PROJECT_NAME: options.project?.name ?? "",
      $PROJECT_SLUG: options.project?.slug ?? "",
      $WORK_ITEM_ID: work.id
    }
  );
}
