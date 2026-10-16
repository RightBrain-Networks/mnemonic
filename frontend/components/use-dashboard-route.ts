"use client";

import { useState } from "react";
import { useBlocker, useLocation } from "@tanstack/react-router";

// Block before a route commits, including browser Back/Forward. The shared
// dashboard layout retains the operation owner and exact retry request.
export function useDashboardRoute(blocked: string | null) {
  const location = useLocation();
  const [rejection, setRejection] = useState<{ message: string; sequence: number } | null>(null);
  useBlocker({
    shouldBlockFn: () => {
      if (!blocked) return false;
      setRejection((previous) => ({ message: blocked, sequence: (previous?.sequence ?? 0) + 1 }));
      return true;
    },
    enableBeforeUnload: Boolean(blocked)
  });
  return {
    pathname: location.pathname,
    query: location.searchStr.replace(/^\?/, ""),
    rejected: rejection
  };
}
