import type { StatusFilter, WorkSort } from "@/lib/types";

export const dashboardStorageKeys = {
  project: "mnemonic.project",
  status: "mnemonic.status",
  sort: "mnemonic.sort"
} as const;

const statusFilters = new Set<StatusFilter>([
  "open",
  "active",
  "dropped",
  "done",
  "wont-do",
  "promoted",
  "all"
]);
const workSorts = new Set<WorkSort>(["updated", "created", "priority"]);

export function dashboardStatusPreference(value: string | null): StatusFilter {
  return statusFilters.has(value as StatusFilter) ? value as StatusFilter : "open";
}

export function dashboardSortPreference(value: string | null): WorkSort {
  return workSorts.has(value as WorkSort) ? value as WorkSort : "updated";
}
