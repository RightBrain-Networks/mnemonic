"use client";
import { useEffect, useRef, useState } from "react";
import { api, ApiError, errorMessage } from "@/lib/api";
import { activityInvalidations, decodeActivityPage, type ActivityInvalidations } from "@/lib/project-activity";

const ALL_VIEWS: ActivityInvalidations = { work: true, reports: true, settings: true, projects: true };
export function useProjectActivity({ projectId, onBootstrap, onInvalidation, onRetryDirty }: {
  projectId: string;
  onBootstrap: (projectId: string) => void;
  onInvalidation: (changes: ActivityInvalidations) => void;
  onRetryDirty: () => void;
}) {
  const [error, setError] = useState("");
  const [streamChanged, setStreamChanged] = useState(false);
  const [restart, setRestart] = useState(0);
  const callbacks = useRef({ onBootstrap, onInvalidation, onRetryDirty });
  callbacks.current = { onBootstrap, onInvalidation, onRetryDirty };
  const wake = useRef<() => void>(() => {});
  useEffect(() => {
    let alive = true;
    let cursor: string | undefined;
    let inFlight = false;
    let wakePending = false;
    let stopped = false;
    let failureCount = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const abort = new AbortController();
    setError("");
    setStreamChanged(false);
    function schedule(delay: number) {
      if (timer !== undefined) clearTimeout(timer);
      if (alive && !stopped && !document.hidden) timer = setTimeout(() => void poll(), delay);
    }
    async function poll() {
      if (!alive || !projectId || stopped || document.hidden) return;
      if (inFlight) { wakePending = true; return; }
      inFlight = true;
      let delay = 15_000;
      try {
        for (let batch = 0; batch < 5; batch += 1) {
          const request = cursor ? { after: cursor, limit: 100 } : { start: "now" as const, limit: 100 };
          const params = new URLSearchParams(cursor ? { after: cursor, limit: "100" } : { start: "now", limit: "100" });
          const raw = await api<unknown>(`/projects/${projectId}/activity?${params}`, {
            signal: AbortSignal.any([abort.signal, AbortSignal.timeout(20_000)])
          });
          const page = decodeActivityPage(raw, projectId, request);
          if (!alive || abort.signal.aborted) return;
          // Invalidations are queued before consuming the cursor; failed view reads retry independently.
          callbacks.current.onInvalidation(cursor ? activityInvalidations(page.items) : ALL_VIEWS);
          if (!cursor) callbacks.current.onBootstrap(projectId);
          cursor = page.next_cursor;
          failureCount = 0;
          setError("");
          if (!page.has_more) break;
          if (batch === 4) delay = 0;
        }
        callbacks.current.onRetryDirty();
      } catch (failure) {
        if (!alive || abort.signal.aborted) return;
        if (failure instanceof ApiError && failure.code === "activity_stream_changed") {
          stopped = true;
          setStreamChanged(true);
          setError("The project activity stream changed. Reload the current snapshot to resume updates.");
        } else {
          failureCount += 1;
          delay = Math.min(30_000, 1_000 * 2 ** Math.min(failureCount - 1, 5));
          delay = Math.round(delay * (0.8 + Math.random() * 0.2));
          setError(errorMessage(failure));
        }
      } finally {
        inFlight = false;
        if (wakePending) { wakePending = false; delay = 0; }
        schedule(delay);
      }
    }
    const catchUp = () => { if (!document.hidden) { if (inFlight) wakePending = true; else schedule(0); } else if (timer !== undefined) clearTimeout(timer); };
    wake.current = catchUp;
    window.addEventListener("focus", catchUp);
    document.addEventListener("visibilitychange", catchUp);
    schedule(0);
    return () => {
      alive = false; abort.abort(); if (timer !== undefined) clearTimeout(timer);
      window.removeEventListener("focus", catchUp); document.removeEventListener("visibilitychange", catchUp);
      wake.current = () => {};
    };
  }, [projectId, restart]);
  return { error, streamChanged, poll: () => wake.current(), reloadSnapshot: () => setRestart((value) => value + 1) };
}
