"use client";

import { useCallback, useState } from "react";
import { movePageOffset, type PageOffsets } from "@/lib/page-offsets";

export function usePageOffset() {
  const [state, setState] = useState<PageOffsets>({ offset: 0, visited: [] });
  const setOffset = useCallback((offset: number) => setState((current) => movePageOffset(current, offset)), []);
  return { offset: state.offset, setOffset, previousOffset: state.visited.at(-1) ?? 0 };
}
