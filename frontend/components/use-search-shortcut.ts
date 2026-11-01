"use client";

import { useEffect, useRef } from "react";
import { dialogOpen, typingTarget } from "@/lib/keyboard-shortcuts";

export function useSearchShortcut() {
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    function focusSearch(event: KeyboardEvent) {
      if (event.key !== "/" || event.ctrlKey || event.metaKey || event.altKey) return;
      if (event.defaultPrevented || typingTarget(event.target) || dialogOpen()) return;
      event.preventDefault();
      input.current?.focus();
    }
    window.addEventListener("keydown", focusSearch);
    return () => window.removeEventListener("keydown", focusSearch);
  }, []);
  return input;
}
