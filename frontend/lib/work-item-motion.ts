export const WORK_ITEM_SLIDE_DURATION_MS = 700;
export const WORK_ITEM_FADE_DURATION_MS = 200;
export const EASE_OUT_CUBIC = "cubic-bezier(0.33, 1, 0.68, 1)";

// easeOutBounce has no cubic-bezier form, so sample its piecewise curve at frame cadence.
const BOUNCE_KEYFRAME_STEPS = 60;

export type WorkItemInsertionPlan = {
  addedIds: string[];
  retainedIds: string[];
};

export function easeOutBounce(progress: number): number {
  if (progress <= 0) return 0;
  if (progress >= 1) return 1;

  const n1 = 7.5625;
  const d1 = 2.75;

  if (progress < 1 / d1) {
    return n1 * progress * progress;
  }
  if (progress < 2 / d1) {
    const shifted = progress - 1.5 / d1;
    return n1 * shifted * shifted + 0.75;
  }
  if (progress < 2.5 / d1) {
    const shifted = progress - 2.25 / d1;
    return n1 * shifted * shifted + 0.9375;
  }

  const shifted = progress - 2.625 / d1;
  return n1 * shifted * shifted + 0.984375;
}

export function workItemSlideKeyframes(deltaY: number): Keyframe[] {
  return Array.from({ length: BOUNCE_KEYFRAME_STEPS + 1 }, (_, index) => {
    const offset = index / BOUNCE_KEYFRAME_STEPS;
    const translateY = index === BOUNCE_KEYFRAME_STEPS
      ? 0
      : deltaY * (1 - easeOutBounce(offset));
    return { transform: `translateY(${translateY}px)`, offset };
  });
}

export function planWorkItemInsertion(
  previousIds: readonly string[],
  previousTotal: number,
  currentIds: readonly string[],
  currentTotal: number
): WorkItemInsertionPlan | null {
  if (currentTotal <= previousTotal) {
    return null;
  }

  const previousSet = new Set(previousIds);
  const currentSet = new Set(currentIds);
  const addedIds = currentIds.filter((id) => !previousSet.has(id));
  if (addedIds.length === 0) return null;

  const retainedIds = currentIds.filter((id) => previousSet.has(id));
  const previouslyOrderedRetainedIds = previousIds.filter((id) => currentSet.has(id));
  const retainedOrderChanged = retainedIds.length !== previouslyOrderedRetainedIds.length
    || retainedIds.some((id, index) => id !== previouslyOrderedRetainedIds[index]);

  return retainedOrderChanged ? null : { addedIds, retainedIds };
}
