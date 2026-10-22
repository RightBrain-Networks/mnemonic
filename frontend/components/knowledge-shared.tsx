"use client";

import { errorMessage } from "@/lib/api";
import { knowledgeNextOffset, type DecisionState, type KnowledgePage } from "@/lib/knowledge";

export function stateLabel(state: DecisionState) { return state.charAt(0).toUpperCase() + state.slice(1).replace("-", " "); }
export function DecisionStateBadge({ state }: { state: DecisionState }) { return <span className={`decision-state decision-state-${state}`}>{stateLabel(state)}</span>; }
export function KnowledgeReadError({ error, retry }: { error: unknown; retry: () => void }) {
  return <div className="error-notice" role="alert"><p>{errorMessage(error)}</p><button type="button" className="button button-secondary" onClick={retry}>Retry loading</button></div>;
}
export function KnowledgePagination<T>({ page, noun, busy = false, onOffset }: { page: KnowledgePage<T>; noun: string; busy?: boolean; onOffset: (offset: number) => void }) {
  const next = knowledgeNextOffset(page);
  return <div className="artifact-pagination"><span>{page.items.length ? `${page.offset + 1}–${page.offset + page.items.length} of ${page.total} ${noun}` : page.total ? `No ${noun} on this page · ${page.total} total` : `0 ${noun}`}</span><div>
    <button type="button" className="button button-secondary" disabled={busy || page.offset === 0} onClick={() => onOffset(Math.max(0, page.offset - page.limit))}>Previous</button>
    <button type="button" className="button button-secondary" disabled={busy || next === null} onClick={() => { if (next !== null) onOffset(next); }}>Next</button>
  </div></div>;
}
