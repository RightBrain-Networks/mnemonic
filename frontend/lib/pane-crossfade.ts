import type { StatusFilterTransition } from "@/lib/work-queue";

// A view transition captures each named element on its own, so these are the two panes a
// lifecycle filter can cross-dissolve independently of the rest of the page. globals.css
// pairs the names with the transition's one adjustable duration and its two easings.
export const PANE_VIEW_TRANSITION_NAMES = {
  queue: "work-queue",
  detail: "work-detail"
} as const;

export type PaneCrossfadeTargets = {
  queue: boolean;
  detail: boolean;
};

// A lifecycle filter renames the queue's result, so the queue always cross-dissolves. The
// detail pane only does when the same change retires the record it was showing; dissolving
// an empty pane into an identical empty pane would dip the surface for nothing.
export function paneCrossfadeTargets(
  transition: StatusFilterTransition,
  queryRenamed: boolean = transition !== "unchanged"
): PaneCrossfadeTargets {
  return {
    queue: queryRenamed,
    detail: transition === "refilter-and-deselect"
  };
}
