export type PageOffsets = { offset: number; visited: number[] };

export function movePageOffset(state: PageOffsets, offset: number): PageOffsets {
  if (offset === 0) return { offset: 0, visited: [] };
  if (offset === state.offset) return state;
  const earlier = state.visited.indexOf(offset);
  return { offset, visited: earlier >= 0 ? state.visited.slice(0, earlier) : [...state.visited, state.offset] };
}

export function nextPageOffset(page: { offset: number; total: number; items: unknown[]; next_offset?: number | null }): number | null {
  if (page.next_offset !== undefined) return page.next_offset;
  const end = page.offset + page.items.length;
  return page.items.length && end < page.total ? end : null;
}
