"use client";

import { useEffect, useRef } from "react";

/** A view owns its failed read until that view succeeds, independently of feed cursors. */
export function useFailedReadRetry({ scope, failed, busy, retry, enabled = true }: {
  scope: string;
  failed: boolean;
  busy: boolean;
  retry: () => void;
  enabled?: boolean;
}): void {
  const callback = useRef(retry);
  callback.current = retry;
  const attempts = useRef(0);
  const previousScope = useRef(scope);
  useEffect(() => {
    if (scope !== previousScope.current) {
      previousScope.current = scope;
      attempts.current = 0;
    }
    // Clearing an error to begin a request is not evidence of a successful read.
    if (!failed && !busy) attempts.current = 0;
    if (!enabled || busy || !failed) return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let fired = false;
    const run = () => {
      if (fired || document.hidden) return;
      fired = true;
      if (timer !== undefined) clearTimeout(timer);
      attempts.current += 1;
      callback.current();
    };
    const delay = Math.round(Math.min(30_000, 1_000 * 2 ** Math.min(attempts.current, 5))
      * (0.8 + Math.random() * 0.2));
    const wake = () => {
      if (document.hidden) {
        if (timer !== undefined) clearTimeout(timer);
        timer = undefined;
      } else run();
    };
    if (!document.hidden) timer = setTimeout(run, delay);
    window.addEventListener("focus", wake);
    document.addEventListener("visibilitychange", wake);
    return () => {
      fired = true;
      if (timer !== undefined) clearTimeout(timer);
      window.removeEventListener("focus", wake);
      document.removeEventListener("visibilitychange", wake);
    };
  }, [scope, failed, busy, enabled]);
}
