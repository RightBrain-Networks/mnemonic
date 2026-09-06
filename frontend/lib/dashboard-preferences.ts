import type { StatusFilter, WorkSort } from "@/lib/types";
import { workSplitPreference } from "./work-split.ts";

export const dashboardStorageKeys = {
  project: "mnemonic.project",
  status: "mnemonic.status",
  sort: "mnemonic.sort",
  libraryTools: "mnemonic.library-tools",
  workSplit: "mnemonic.work-split"
} as const;

export const libraryToolsInitializationScript = `(() => {
  let state = "open";
  try {
    if (localStorage.getItem("${dashboardStorageKeys.libraryTools}") === "closed") state = "closed";
  } catch {}
  document.documentElement.dataset.libraryTools = state;
})();`;

const statusFilters = new Set<StatusFilter>([
  "pending",
  "active",
  "dropped",
  "deferred",
  "done",
  "wont-do",
  "promoted",
  "all"
]);
const workSorts = new Set<WorkSort>(["updated", "created", "priority"]);

export function dashboardStatusPreference(value: string | null): StatusFilter {
  if (value === "open") return "pending";
  return statusFilters.has(value as StatusFilter) ? value as StatusFilter : "pending";
}

export function dashboardSortPreference(value: string | null): WorkSort {
  return workSorts.has(value as WorkSort) ? value as WorkSort : "updated";
}

export function dashboardLibraryToolsPreference(value: string | null): boolean {
  return value !== "closed";
}

// The queue's share of the work surface, or null when the stylesheet defaults should apply.
export function dashboardWorkSplitPreference(value: string | null): number | null {
  return workSplitPreference(value);
}
