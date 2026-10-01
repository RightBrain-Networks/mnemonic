"use client";

import { useCallback, useEffect, useState } from "react";
import { decodePinnedWork, MAX_PINNED_WORK, pinnedWorkKey } from "@/lib/pinned-work";

const EMPTY: string[] = [];

export function usePinnedWork(projectId: string) {
  const [state, setState] = useState<{ projectId: string; ids: string[] } | null>(null);
  useEffect(() => {
    const key = pinnedWorkKey(projectId);
    function read() {
      let ids: string[] = [];
      try { ids = decodePinnedWork(localStorage.getItem(key)); } catch { /* Storage is optional. */ }
      setState({ projectId, ids });
    }
    read();
    function sync(event: StorageEvent) { if (event.key === key || event.key === null) read(); }
    window.addEventListener("storage", sync);
    return () => window.removeEventListener("storage", sync);
  }, [projectId]);
  const toggle = useCallback((id: string) => {
    setState((current) => {
      if (current?.projectId !== projectId) return current;
      const ids = current.ids.includes(id) ? current.ids.filter((value) => value !== id)
        : current.ids.length < MAX_PINNED_WORK ? [...current.ids, id] : current.ids;
      try { localStorage.setItem(pinnedWorkKey(projectId), JSON.stringify(ids)); } catch { /* Keep the preference for this visit. */ }
      return { projectId, ids };
    });
  }, [projectId]);
  return { ids: state?.projectId === projectId ? state.ids : EMPTY, ready: state?.projectId === projectId, toggle };
}
