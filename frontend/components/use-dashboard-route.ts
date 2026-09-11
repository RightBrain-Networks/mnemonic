"use client";

import { useLayoutEffect, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

// Keep the component that owns an outstanding operation mounted even when
// navigation originates from browser history instead of a guarded menu link.
export function useDashboardRoute(blocked: string | null) {
  const pathname = usePathname();
  const query = useSearchParams().toString();
  const router = useRouter();
  const requested = pathname + (query ? `?${query}` : "");
  const [route, setRoute] = useState(requested);
  const rejected = route !== requested ? blocked : null;

  useLayoutEffect(() => {
    if (route === requested) return;
    if (blocked) {
      router.replace(route, { scroll: false });
      return;
    }
    setRoute(requested);
  }, [blocked, requested, route, router]);

  const separator = route.indexOf("?");
  return {
    pathname: separator < 0 ? route : route.slice(0, separator),
    query: separator < 0 ? "" : route.slice(separator + 1),
    rejected
  };
}
