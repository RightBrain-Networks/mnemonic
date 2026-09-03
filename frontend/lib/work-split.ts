// The work surface splits its width between the queue and the detail pane. The split is the
// queue's share as a percentage of the surface width; the stylesheet clamps the rendered
// columns so neither pane collapses below its readable minimum.

export const WORK_SPLIT_DEFAULT = 35;
export const WORK_SPLIT_MIN = 20;
export const WORK_SPLIT_MAX = 70;
export const WORK_SPLIT_STEP = 2;

export type WorkSplitStep = "left" | "right" | "start" | "end";

export function clampWorkSplit(value: number): number {
  if (!Number.isFinite(value)) return WORK_SPLIT_DEFAULT;
  const bounded = Math.min(WORK_SPLIT_MAX, Math.max(WORK_SPLIT_MIN, value));
  return Math.round(bounded * 10) / 10;
}

// Parses a stored preference; null means "not customised" so the stylesheet defaults apply.
export function workSplitPreference(value: string | null): number | null {
  if (value === null || value.trim() === "") return null;
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return null;
  return clampWorkSplit(parsed);
}

export function workSplitFromPointer({
  pointerX,
  left,
  width
}: {
  pointerX: number;
  left: number;
  width: number;
}): number {
  if (!(width > 0)) return WORK_SPLIT_DEFAULT;
  return clampWorkSplit(((pointerX - left) / width) * 100);
}

export function stepWorkSplit(current: number, step: WorkSplitStep): number {
  switch (step) {
    case "left": return clampWorkSplit(current - WORK_SPLIT_STEP);
    case "right": return clampWorkSplit(current + WORK_SPLIT_STEP);
    case "start": return WORK_SPLIT_MIN;
    case "end": return WORK_SPLIT_MAX;
  }
}
