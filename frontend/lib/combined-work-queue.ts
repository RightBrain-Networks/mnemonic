import type { WorkQueueItem } from "./work-queue.ts";
import type { WorkSort } from "./types.ts";
import { compareUtcDateTimes } from "./wire-guards.ts";

export function queueItemId(item: WorkQueueItem): string {
  return "review_task" in item ? item.review_task.id : item.summary.work_item.id;
}

export function queueOrder(sort: WorkSort, pins: readonly string[]) {
  return (left: WorkQueueItem, right: WorkQueueItem): number => {
    const a = "review_task" in left ? left.review_task : left.summary.work_item;
    const b = "review_task" in right ? right.review_task : right.summary.work_item;
    const pinned = Number(pins.includes(b.id)) - Number(pins.includes(a.id));
    if (pinned) return pinned;
    if (sort === "priority" && a.priority !== b.priority) return (b.priority ?? 0) - (a.priority ?? 0);
    const date = sort === "created" ? "created_at" : "updated_at";
    return compareUtcDateTimes(b[date] ?? b.updated_at, a[date] ?? a.updated_at)
      || b.id.localeCompare(a.id);
  };
}

// Only publish the prefix whose order is known in both independently paged sources.
// A source's last loaded row bounds its unseen rows; the next fetch extends that bound.
export function combineQueuePages(work: WorkQueueItem[], reviews: WorkQueueItem[], options: {
  sort: WorkSort; pins: readonly string[]; workHasMore: boolean; reviewsHaveMore: boolean; semanticSearch: boolean;
}): WorkQueueItem[] {
  if (options.semanticSearch) return options.workHasMore ? work : [...work, ...reviews];
  const compare = queueOrder(options.sort, options.pins);
  const boundaries = [options.workHasMore ? work.at(-1) : undefined,
    options.reviewsHaveMore ? reviews.at(-1) : undefined].filter((row): row is WorkQueueItem => Boolean(row));
  return [...work, ...reviews].sort(compare).filter((row) => boundaries.every((end) => compare(row, end) <= 0));
}
