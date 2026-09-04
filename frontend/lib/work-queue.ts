import type {
  DuplicateScope,
  HierarchySummary,
  Page,
  StatusFilter,
  WorkSearchHit,
  WorkSort
} from "@/lib/types";

export const WORK_PAGE_SIZE = 20;

export type WorkQueueItem = WorkSearchHit | HierarchySummary;

export const statusFilterLabels: Record<StatusFilter, string> = {
  pending: "Pending",
  active: "Active",
  dropped: "Dropped",
  deferred: "Deferred",
  done: "Done",
  "wont-do": "Won’t do",
  promoted: "Promoted",
  all: "All"
};

// The lifecycle filters in the order the filter row renders them. The left and right
// arrow keys walk this same list, so the row and the shortcut can never disagree.
export const statusFilterOrder: StatusFilter[] = [
  "pending", "active", "dropped", "deferred", "done", "wont-do", "promoted", "all"
];

export type StatusFilterStep = "previous" | "next";

// The filters are one small closed ring, so walking off either end returns to the other
// rather than dead-ending on Pending or All the way the queue's own arrows clamp.
export function cycleStatusFilter(current: StatusFilter, step: StatusFilterStep): StatusFilter {
  const index = statusFilterOrder.indexOf(current);
  if (index === -1) return statusFilterOrder[0];
  const offset = step === "next" ? 1 : statusFilterOrder.length - 1;
  return statusFilterOrder[(index + offset) % statusFilterOrder.length];
}

export type StatusFilterTransition = "unchanged" | "refilter" | "refilter-and-deselect";

// The open detail pane must never outlive the queue that produced it: a record
// shown only under Pending would otherwise stay open beneath a Deferred list.
// Reselecting the current filter is not a change and must leave the pane alone.
export function statusFilterTransition(
  current: StatusFilter,
  next: StatusFilter,
  selectedId: string | null
): StatusFilterTransition {
  if (next === current) return "unchanged";
  return selectedId === null ? "refilter" : "refilter-and-deselect";
}

type WorkQueueEntry = { summary: { work_item: { id: string } } };

export type MergedWorkPages<T> = {
  // Unique entries in wire order; a record that shifted across a page boundary appears once.
  items: T[];
  // Raw count of entries the server returned so far; the next page offset and hasMore use it.
  loaded: number;
  total: number;
};

function dedupeWorkEntries<T extends WorkQueueEntry>(entries: readonly T[]): T[] {
  const seen = new Set<string>();
  const unique: T[] = [];
  for (const entry of entries) {
    const key = entry.summary.work_item.id.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    unique.push(entry);
  }
  return unique;
}

export function loadedOffsets(loadedCount: number, pageSize: number): number[] {
  if (!Number.isFinite(pageSize) || pageSize <= 0) return [0];
  const pages = Math.max(1, Math.ceil(Math.max(0, loadedCount) / pageSize));
  return Array.from({ length: pages }, (_, index) => index * pageSize);
}

export function mergeWorkPages<T extends WorkQueueEntry>(
  pages: ReadonlyArray<Page<T>>
): MergedWorkPages<T> {
  const total = pages[0]?.total ?? 0;
  let end = pages.length;
  while (end > 0 && pages[end - 1].items.length === 0) end -= 1;
  const kept = pages.slice(0, end);
  return {
    items: dedupeWorkEntries(kept.flatMap((page) => page.items)),
    loaded: kept.reduce((count, page) => count + page.items.length, 0),
    total
  };
}

export function appendWorkPage<T extends WorkQueueEntry>(
  current: MergedWorkPages<T>,
  page: Page<T>
): MergedWorkPages<T> {
  return {
    items: dedupeWorkEntries([...current.items, ...page.items]),
    loaded: current.loaded + page.items.length,
    total: page.total
  };
}

export function hasMoreWork(loaded: number, total: number | null): boolean {
  return total !== null && loaded < total;
}

export function moreFiltersForced(input: {
  duplicateScope: DuplicateScope;
  canonicalWorkItemId: string;
  tag: string;
  sourceClient: string;
  sourceSessionId: string;
}): boolean {
  return input.duplicateScope !== "canonical"
    || Boolean(input.canonicalWorkItemId)
    || input.tag.trim().length > 0
    || input.sourceClient.trim().length > 0
    || input.sourceSessionId.trim().length > 0;
}

export function resultCountLabel(input: {
  loading: boolean;
  pendingQuery: boolean;
  flatSearch: boolean;
  total: number | null;
}): string {
  if (input.loading || input.pendingQuery) return "Finding work…";
  if (input.total === null) return "";
  return input.flatSearch
    ? `${input.total} work record${input.total === 1 ? "" : "s"}`
    : `${input.total} root branch${input.total === 1 ? "" : "es"}`;
}

export function sortDescription(sort: WorkSort): string {
  switch (sort) {
    case "created": return "Sorted by creation";
    case "priority": return "Sorted by priority";
    default: return "Sorted by last activity";
  }
}

export type QueueDirection = "up" | "down";

export function nextQueueSelection(
  ids: readonly string[],
  selectedId: string | null,
  direction: QueueDirection
): string | null {
  if (!ids.length) return null;
  const index = selectedId === null ? -1 : ids.indexOf(selectedId);
  if (index === -1) return ids[0];
  const next = direction === "down"
    ? Math.min(ids.length - 1, index + 1)
    : Math.max(0, index - 1);
  return ids[next];
}

export function listScrollTopFor({
  listTop,
  listHeight,
  scrollTop,
  optionTop,
  optionHeight,
  padding = 8
}: {
  listTop: number;
  listHeight: number;
  scrollTop: number;
  optionTop: number;
  optionHeight: number;
  padding?: number;
}): number {
  const optionOffset = optionTop - listTop + scrollTop;
  const top = optionOffset - padding;
  const bottom = optionOffset + optionHeight + padding;
  if (top < scrollTop) return Math.max(0, top);
  if (bottom > scrollTop + listHeight) return Math.max(0, Math.min(top, bottom - listHeight));
  return scrollTop;
}
