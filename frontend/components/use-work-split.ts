"use client";

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
  type PointerEvent,
  type RefObject
} from "react";
import { dashboardStorageKeys, dashboardWorkSplitPreference } from "@/lib/dashboard-preferences";
import {
  WORK_SPLIT_DEFAULT,
  stepWorkSplit,
  workSplitFromPointer,
  type WorkSplitStep
} from "@/lib/work-split";

// Below this width the stylesheet widens the queue's default share to 40%.
const COMPACT_SURFACE_MEDIA = "(max-width: 1100px)";
const COMPACT_DEFAULT_SPLIT = 40;
const SPLIT_VARIABLE = "--work-split";

const keyboardSteps: Record<string, WorkSplitStep> = {
  ArrowLeft: "left",
  ArrowRight: "right",
  Home: "start",
  End: "end"
};

export type WorkSplitSeparatorProps = {
  onPointerDown: (event: PointerEvent<HTMLElement>) => void;
  onPointerMove: (event: PointerEvent<HTMLElement>) => void;
  onPointerUp: (event: PointerEvent<HTMLElement>) => void;
  onPointerCancel: (event: PointerEvent<HTMLElement>) => void;
  onKeyDown: (event: KeyboardEvent<HTMLElement>) => void;
  onDoubleClick: () => void;
};

export type WorkSplit<T extends HTMLElement> = {
  surfaceRef: RefObject<T | null>;
  // The queue's share of the surface width: the stored preference, else the stylesheet default.
  split: number;
  // Present only while a preference is stored, so the stylesheet defaults apply otherwise.
  surfaceStyle: CSSProperties | undefined;
  resizing: boolean;
  separatorProps: WorkSplitSeparatorProps;
  reset: () => void;
};

function readStoredSplit(): number | null {
  try {
    return dashboardWorkSplitPreference(localStorage.getItem(dashboardStorageKeys.workSplit));
  } catch {
    return null;
  }
}

function storeSplit(value: number | null): void {
  try {
    if (value === null) localStorage.removeItem(dashboardStorageKeys.workSplit);
    else localStorage.setItem(dashboardStorageKeys.workSplit, String(value));
  } catch {
    // The split is a convenience; storage being unavailable only loses persistence.
  }
}

// Owns the draggable split between the queue and the detail pane. During a drag the
// variable is written straight to the surface element so the columns follow the pointer
// without re-rendering the queue; the value is committed and stored when the drag ends.
export function useWorkSplit<T extends HTMLElement>(): WorkSplit<T> {
  const surfaceRef = useRef<T>(null);
  const [stored, setStored] = useState<number | null>(null);
  const [compact, setCompact] = useState(false);
  const [resizing, setResizing] = useState(false);
  const drag = useRef<{ pointerId: number; left: number; width: number; latest: number } | null>(null);

  useEffect(() => {
    setStored(readStoredSplit());
    if (typeof window.matchMedia !== "function") return;
    const media = window.matchMedia(COMPACT_SURFACE_MEDIA);
    const update = () => setCompact(media.matches);
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);

  const commit = useCallback((value: number | null) => {
    setStored(value);
    storeSplit(value);
    const surface = surfaceRef.current;
    if (!surface) return;
    if (value === null) surface.style.removeProperty(SPLIT_VARIABLE);
    else surface.style.setProperty(SPLIT_VARIABLE, String(value));
  }, []);

  const split = stored ?? (compact ? COMPACT_DEFAULT_SPLIT : WORK_SPLIT_DEFAULT);

  const onPointerDown = useCallback((event: PointerEvent<HTMLElement>) => {
    if (event.button !== 0) return;
    const surface = surfaceRef.current;
    if (!surface) return;
    const rect = surface.getBoundingClientRect();
    drag.current = { pointerId: event.pointerId, left: rect.left, width: rect.width, latest: split };
    event.currentTarget.setPointerCapture(event.pointerId);
    event.preventDefault();
    setResizing(true);
  }, [split]);

  const onPointerMove = useCallback((event: PointerEvent<HTMLElement>) => {
    const active = drag.current;
    const surface = surfaceRef.current;
    if (!active || active.pointerId !== event.pointerId || !surface) return;
    const next = workSplitFromPointer({ pointerX: event.clientX, left: active.left, width: active.width });
    active.latest = next;
    surface.style.setProperty(SPLIT_VARIABLE, String(next));
  }, []);

  const endDrag = useCallback((event: PointerEvent<HTMLElement>) => {
    const active = drag.current;
    if (!active || active.pointerId !== event.pointerId) return;
    drag.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    setResizing(false);
    commit(active.latest);
  }, [commit]);

  const onKeyDown = useCallback((event: KeyboardEvent<HTMLElement>) => {
    const step = keyboardSteps[event.key];
    if (!step) return;
    event.preventDefault();
    commit(stepWorkSplit(split, step));
  }, [commit, split]);

  const reset = useCallback(() => commit(null), [commit]);

  return {
    surfaceRef,
    split,
    surfaceStyle: stored === null ? undefined : { [SPLIT_VARIABLE]: String(stored) } as CSSProperties,
    resizing,
    separatorProps: {
      onPointerDown,
      onPointerMove,
      onPointerUp: endDrag,
      onPointerCancel: endDrag,
      onKeyDown,
      onDoubleClick: reset
    },
    reset
  };
}
