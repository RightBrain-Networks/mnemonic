"use client";

import { useCallback, useRef, type RefObject } from "react";
import { flushSync } from "react-dom";
import { PANE_VIEW_TRANSITION_NAMES, type PaneCrossfadeTargets } from "@/lib/pane-crossfade";

// Marks the document for the stylesheet: a filter's transition holds the root still,
// while the theme's own transition still crossfades it.
const DOCUMENT_ATTRIBUTE = "data-pane-crossfade";

type ViewTransition = { finished: Promise<void> };
type ViewTransitionDocument = Document & {
  startViewTransition?: (update: () => void) => ViewTransition;
};

export type PaneCrossfade = {
  queueRef: RefObject<HTMLDivElement | null>;
  detailRef: RefObject<HTMLElement | null>;
  // Applies the filter change inside a cross-dissolve of the panes it renames. The update
  // has to run in here: the browser captures the outgoing panes around it, and only a
  // synchronous commit lands the new result between the two captures.
  run: (targets: PaneCrossfadeTargets, update: () => void) => void;
};

export function usePaneCrossfade(): PaneCrossfade {
  const queueRef = useRef<HTMLDivElement>(null);
  const detailRef = useRef<HTMLElement>(null);
  const generationRef = useRef(0);

  const run = useCallback((targets: PaneCrossfadeTargets, update: () => void) => {
    const named: [HTMLElement | null, string][] = [
      [targets.queue ? queueRef.current : null, PANE_VIEW_TRANSITION_NAMES.queue],
      [targets.detail ? detailRef.current : null, PANE_VIEW_TRANSITION_NAMES.detail]
    ];
    const dissolving = named.filter((entry): entry is [HTMLElement, string] => entry[0] !== null);
    const start = (document as ViewTransitionDocument).startViewTransition;
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (!start || reducedMotion || !dissolving.length) {
      update();
      return;
    }
    // Naming a pane captures it on its own; everything else swaps with the root, which the
    // stylesheet holds still so the filter button answers the click immediately.
    for (const [pane, name] of dissolving) pane.style.setProperty("view-transition-name", name);
    document.documentElement.setAttribute(DOCUMENT_ATTRIBUTE, "");
    const generation = ++generationRef.current;
    const transition = start.call(document, () => flushSync(update));
    void transition.finished.catch(() => undefined).then(() => {
      // A newer transition already owns the names, and clearing them here would strip the
      // panes it is still capturing.
      if (generationRef.current !== generation) return;
      for (const [pane] of dissolving) pane.style.removeProperty("view-transition-name");
      document.documentElement.removeAttribute(DOCUMENT_ATTRIBUTE);
    });
  }, []);

  return { queueRef, detailRef, run };
}
