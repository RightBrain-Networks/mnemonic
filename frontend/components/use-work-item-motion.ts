"use client";

import { useLayoutEffect, useRef, type RefObject } from "react";
import {
  EASE_IN_OUT_QUINT,
  WORK_ITEM_FADE_DURATION_MS,
  WORK_ITEM_SLIDE_DURATION_MS,
  planWorkItemMotion,
  workItemSlideKeyframes
} from "@/lib/work-item-motion";

type ItemSnapshot = {
  element: HTMLElement;
  documentLeft: number;
  documentTop: number;
  width: number;
  height: number;
  opacity: string;
};

type MotionSnapshot = {
  viewKey: string;
  itemIds: string[];
  total: number;
  items: Map<string, ItemSnapshot>;
  // The nearest scrolling ancestor of the list; positions are recorded relative to its content.
  scroller: HTMLElement | null;
};

type EnteringState = {
  element: HTMLElement;
  wasInert: boolean;
  inlineOpacity: string;
  hadEnteringClass: boolean;
};

type ExitingState = {
  element: HTMLElement;
  reposition: () => void;
  scroller: HTMLElement | null;
};

type MotionOptions = {
  itemIds: readonly string[];
  total: number | null;
  viewKey: string;
  revision: unknown;
  snapshotSignal?: unknown;
  enabled?: boolean;
};

function directWorkItems(list: HTMLElement): Map<string, HTMLElement> {
  const items = new Map<string, HTMLElement>();
  for (const child of list.children) {
    if (!(child instanceof HTMLElement)) continue;
    const id = child.dataset.workItemId;
    if (id) items.set(id, child);
  }
  return items;
}

function scrollingAncestor(element: HTMLElement): HTMLElement | null {
  let node = element.parentElement;
  while (node && node !== document.body) {
    const { overflowY } = getComputedStyle(node);
    if (overflowY === "auto" || overflowY === "scroll") return node;
    node = node.parentElement;
  }
  return null;
}

function documentTop(element: HTMLElement, scroller: HTMLElement | null): number {
  return element.getBoundingClientRect().top + window.scrollY + (scroller?.scrollTop ?? 0);
}

function sameIds(left: readonly string[], right: readonly string[]): boolean {
  return left.length === right.length && left.every((id, index) => id === right[index]);
}

export function useWorkItemMotion<T extends HTMLElement>({
  itemIds,
  total,
  viewKey,
  revision,
  snapshotSignal,
  enabled = true
}: MotionOptions): RefObject<T | null> {
  const listRef = useRef<T>(null);
  const snapshotRef = useRef<MotionSnapshot | null>(null);
  const animationsRef = useRef(new Set<Animation>());
  const enteringRef = useRef(new Map<string, EnteringState>());
  const exitingRef = useRef(new Map<string, ExitingState>());
  const generationRef = useRef(0);
  const itemIdsKey = itemIds.join("\u0000");

  function cancelAnimations() {
    for (const animation of animationsRef.current) animation.cancel();
    animationsRef.current.clear();
  }

  function restoreEntering(state: EnteringState) {
    if (state.inlineOpacity) state.element.style.opacity = state.inlineOpacity;
    else state.element.style.removeProperty("opacity");
    state.element.classList.toggle("work-item-entering", state.hadEnteringClass);
    state.element.inert = state.wasInert;
  }

  function releaseEntering() {
    for (const state of enteringRef.current.values()) {
      restoreEntering(state);
    }
    enteringRef.current.clear();
  }

  function releaseExiting() {
    for (const state of exitingRef.current.values()) removeExiting(state);
    exitingRef.current.clear();
  }

  function cancelMotion() {
    cancelAnimations();
    releaseEntering();
    releaseExiting();
  }

  useLayoutEffect(() => {
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
    const handleReducedMotion = (event: MediaQueryListEvent) => {
      if (!event.matches) return;
      ++generationRef.current;
      cancelMotion();
    };
    reducedMotion.addEventListener("change", handleReducedMotion);
    return () => {
      reducedMotion.removeEventListener("change", handleReducedMotion);
      ++generationRef.current;
      cancelMotion();
    };
  }, []);

  useLayoutEffect(() => {
    const list = listRef.current;
    const previous = snapshotRef.current;
    const sameResult = previous?.viewKey === viewKey
      && previous.total === total
      && sameIds(previous.itemIds, itemIds);
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const motionActive = animationsRef.current.size > 0
      || enteringRef.current.size > 0
      || exitingRef.current.size > 0;
    if (sameResult && motionActive && list && enabled && !reducedMotion) return;

    const generation = ++generationRef.current;
    const scroller = list ? scrollingAncestor(list) : null;
    const elementsBeforeCancel = list ? directWorkItems(list) : new Map<string, HTMLElement>();
    const visualTops = new Map(
      [...elementsBeforeCancel].map(([id, element]) => [id, documentTop(element, scroller)])
    );
    const plan = list && total !== null && previous?.viewKey === viewKey
      && enabled && !reducedMotion
      ? planWorkItemMotion(previous.itemIds, previous.total, itemIds, total)
      : null;

    cancelAnimations();
    releaseExiting();

    if (!list || total === null) {
      releaseEntering();
      snapshotRef.current = null;
      return;
    }

    const elements = directWorkItems(list);
    const items = itemSnapshots(elements, scroller);
    snapshotRef.current = { viewKey, itemIds: [...itemIds], total, items, scroller };

    if (!plan || !previous) {
      releaseEntering();
      return;
    }

    const pendingIds = new Set([...enteringRef.current.keys(), ...plan.addedIds]);
    for (const id of pendingIds) {
      const element = elements.get(id);
      const existing = enteringRef.current.get(id);
      if (!element) {
        if (existing) restoreEntering(existing);
        enteringRef.current.delete(id);
        continue;
      }
      if (existing?.element !== element) {
        if (existing) restoreEntering(existing);
        enteringRef.current.set(id, {
          element,
          wasInert: element.inert,
          inlineOpacity: element.style.opacity,
          hadEnteringClass: element.classList.contains("work-item-entering")
        });
      }
      element.style.opacity = "0";
      element.classList.add("work-item-entering");
      element.inert = true;
    }

    for (const id of plan.removedIds) {
      const item = previous.items.get(id);
      if (item) exitingRef.current.set(id, createExitingState(id, item, previous.scroller));
    }

    const slideAnimations: Animation[] = [];
    for (const id of plan.retainedIds) {
      const element = elements.get(id);
      const previousTop = previous.items.get(id)?.documentTop;
      const currentTop = items.get(id)?.documentTop;
      const visualTop = visualTops.get(id);
      if (!element || enteringRef.current.has(id) || previousTop === undefined
        || currentTop === undefined || visualTop === undefined) continue;
      // Remove the newest layout shift from the still-animated visual position.
      const layoutShift = currentTop - previousTop;
      const deltaY = visualTop - layoutShift - currentTop;
      if (deltaY >= -0.5) continue;
      const animation = element.animate(workItemSlideKeyframes(deltaY), {
        duration: WORK_ITEM_SLIDE_DURATION_MS,
        easing: "linear",
        fill: "both"
      });
      animationsRef.current.add(animation);
      slideAnimations.push(animation);
    }

    void Promise.all(slideAnimations.map((animation) => animation.finished.catch(() => undefined)))
      .then(() => {
        if (generationRef.current !== generation) return;
        for (const animation of slideAnimations) {
          animationsRef.current.delete(animation);
          animation.cancel();
        }
        const fades: Animation[] = [];
        const entering = [...enteringRef.current.entries()];
        for (const [id, state] of entering) {
          if (!state.element.isConnected) {
            restoreEntering(state);
            enteringRef.current.delete(id);
            continue;
          }
          const animation = state.element.animate([{ opacity: 0 }, { opacity: 1 }], {
            duration: WORK_ITEM_FADE_DURATION_MS,
            easing: EASE_IN_OUT_QUINT,
            fill: "both"
          });
          animationsRef.current.add(animation);
          fades.push(animation);
        }
        const exiting = [...exitingRef.current.entries()];
        for (const [id, state] of exiting) {
          if (!state.element.isConnected) {
            removeExiting(state);
            exitingRef.current.delete(id);
            continue;
          }
          const animation = state.element.animate([{ opacity: 1 }, { opacity: 0 }], {
            duration: WORK_ITEM_FADE_DURATION_MS,
            easing: EASE_IN_OUT_QUINT,
            fill: "both"
          });
          animationsRef.current.add(animation);
          fades.push(animation);
        }
        void Promise.all(fades.map((animation) => animation.finished.catch(() => undefined)))
          .then(() => {
            if (generationRef.current !== generation) return;
            for (const [id, state] of entering) {
              if (enteringRef.current.get(id) !== state) continue;
              restoreEntering(state);
              enteringRef.current.delete(id);
            }
            for (const [id, state] of exiting) {
              if (exitingRef.current.get(id) !== state) continue;
              removeExiting(state);
              exitingRef.current.delete(id);
            }
            for (const animation of fades) {
              animationsRef.current.delete(animation);
              animation.cancel();
            }
          });
      });

  }, [enabled, itemIdsKey, revision, snapshotSignal, total, viewKey]);

  return listRef;
}

function itemSnapshots(
  elements: Map<string, HTMLElement>,
  scroller: HTMLElement | null
): Map<string, ItemSnapshot> {
  return new Map([...elements].map(([id, element]) => {
    const rect = element.getBoundingClientRect();
    return [id, {
      element,
      documentLeft: rect.left + window.scrollX + (scroller?.scrollLeft ?? 0),
      documentTop: rect.top + window.scrollY + (scroller?.scrollTop ?? 0),
      width: rect.width,
      height: rect.height,
      opacity: getComputedStyle(element).opacity
    }];
  }));
}

function removeDuplicateIds(element: HTMLElement) {
  element.removeAttribute("id");
  for (const descendant of element.querySelectorAll("[id]")) {
    descendant.removeAttribute("id");
  }
}

function createExitingState(
  id: string,
  snapshot: ItemSnapshot,
  scroller: HTMLElement | null
): ExitingState {
  const element = snapshot.element.cloneNode(true) as HTMLElement;
  removeDuplicateIds(element);
  element.removeAttribute("data-work-item-id");
  element.dataset.workItemExitId = id;
  element.classList.add("work-item-exiting");
  element.setAttribute("aria-hidden", "true");
  element.inert = true;
  Object.assign(element.style, {
    height: `${snapshot.height}px`,
    left: "0",
    margin: "0",
    opacity: snapshot.opacity,
    pointerEvents: "none",
    position: "fixed",
    top: "0",
    width: `${snapshot.width}px`,
    zIndex: "5"
  });
  const reposition = () => {
    element.style.transform = `translate(${
      snapshot.documentLeft - window.scrollX - (scroller?.scrollLeft ?? 0)
    }px, ${snapshot.documentTop - window.scrollY - (scroller?.scrollTop ?? 0)}px)`;
  };
  reposition();
  document.body.append(element);
  window.addEventListener("scroll", reposition, { passive: true });
  window.addEventListener("resize", reposition);
  scroller?.addEventListener("scroll", reposition, { passive: true });
  return { element, reposition, scroller };
}

function removeExiting(state: ExitingState) {
  window.removeEventListener("scroll", state.reposition);
  window.removeEventListener("resize", state.reposition);
  state.scroller?.removeEventListener("scroll", state.reposition);
  state.element.remove();
}
