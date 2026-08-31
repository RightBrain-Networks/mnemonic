import type { RefObject } from "react";
import WorkItemCard from "@/components/work-item-card";
import type { Page, StatusFilter, WorkSummary } from "@/lib/types";
import { workRecallPointer } from "@/lib/work-recall-pointer";

const WORK_PAGE_SIZE = 20;
const filters: StatusFilter[] = ["open", "done", "wont-do", "promoted", "all"];
const filterLabels: Record<StatusFilter, string> = {
  open: "Open",
  done: "Done",
  "wont-do": "Won’t do",
  promoted: "Promoted",
  all: "All"
};

const iconPaths = {
  search: "m21 21-4.4-4.4M19 10.5a8.5 8.5 0 1 1-17 0 8.5 8.5 0 0 1 17 0Z",
  close: "m6 6 12 12M6 18 18 6",
  plus: "M12 5v14M5 12h14",
  box: "M4 8h16v13H4V8ZM2 3h20v5H2V3Zm7 10h6",
  back: "M19 12H5m5-5-5 5 5 5",
  arrow: "M5 12h14m-5-5 5 5-5 5"
};

function Icon({ name, size = 18 }: { name: keyof typeof iconPaths; size?: number }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={iconPaths[name]} /></svg>;
}

type Props = {
  query: string;
  searchedQuery: string;
  searchRef: RefObject<HTMLInputElement | null>;
  semantic: boolean;
  status: StatusFilter;
  results: Page<WorkSummary> | null;
  loading: boolean;
  error: string;
  offset: number;
  copiedKey: string | null;
  onQuery: (value: string) => void;
  onToggleSemantic: () => void;
  onStatus: (status: StatusFilter) => void;
  onRetry: () => void;
  onClearFilters: () => void;
  onCreate: () => void;
  onOpen: (summary: WorkSummary) => void;
  onEdit: (summary: WorkSummary) => void;
  onDelete: (summary: WorkSummary) => void;
  onCopy: (value: string, key: string, success: string) => void;
  onOffset: (offset: number) => void;
};

export default function WorkItemList({
  query,
  searchedQuery,
  searchRef,
  semantic,
  status,
  results,
  loading,
  error,
  offset,
  copiedKey,
  onQuery,
  onToggleSemantic,
  onStatus,
  onRetry,
  onClearFilters,
  onCreate,
  onOpen,
  onEdit,
  onDelete,
  onCopy,
  onOffset
}: Props) {
  return <>
    <section className="library-controls" aria-label="Find work items">
      <div className="search-field">
        <Icon name="search" size={20} />
        <input ref={searchRef} type="search" value={query} maxLength={500} aria-label="Search work items" placeholder="Search objectives, checkpoints, or session IDs…" onChange={(event) => onQuery(event.target.value)} />
        {query ? <button className="icon-button" type="button" aria-label="Clear search" onClick={() => onQuery("")}><Icon name="close" size={16} /></button> : <kbd aria-hidden="true">/</kbd>}
        <span className="search-mode-divider" />
        <button className={`semantic-toggle ${semantic ? "selected" : ""}`} type="button" aria-label="Semantic search" aria-pressed={semantic} onClick={onToggleSemantic}><span className="semantic-switch"><span /></span><span>Semantic</span></button>
      </div>
      <div className="filter-row">
        <div className="status-filters" role="group" aria-label="Filter by lifecycle">
          {filters.map((filter) => <button type="button" key={filter} className={`filter-button ${status === filter ? "selected" : ""}`} aria-pressed={status === filter} onClick={() => onStatus(filter)}>{filter === "open" && <span className="filter-dot" />}{filterLabels[filter]}</button>)}
        </div>
        <span className="result-count" role="status">{loading || query !== searchedQuery ? "Finding work…" : results ? `${results.total} work item${results.total === 1 ? "" : "s"}` : ""}</span>
      </div>
    </section>

    {error ? <div className="error-notice" role="alert"><p>{error}</p><button className="button button-secondary" type="button" onClick={onRetry}>Try again</button></div> :
      loading ? <div className="card-skeletons" role="status" aria-label="Loading work items">{[1, 2, 3].map((item) => <div className="card-skeleton" key={item}><span /><span /><span /></div>)}</div> :
      !results?.items.length ? <section className="empty-state"><div className="empty-art"><Icon name={searchedQuery ? "search" : "box"} size={31} /><span /></div><h2>{searchedQuery ? "No matching work." : status === "open" ? "No open work yet." : status === "all" ? "No work yet." : `No ${filterLabels[status].toLowerCase()} work.`}</h2><p>{searchedQuery ? "Try another phrase or search across all lifecycle states." : "Create a durable objective here, or ask a connected agent to create one with its first checkpoint."}</p>{searchedQuery || status !== "open" ? <button type="button" className="button button-secondary" onClick={onClearFilters}>Clear filters</button> : <button type="button" className="button button-primary" onClick={onCreate}><Icon name="plus" size={16} />Create work</button>}</section> :
      <section className="handoff-list" aria-label="Durable work items">{results.items.map((item) => <WorkItemCard key={item.work_item.id} summary={item} copied={copiedKey === `${item.work_item.id}:pointer`} onOpen={() => onOpen(item)} onEdit={() => onEdit(item)} onDelete={() => onDelete(item)} onCopyPointer={() => onCopy(workRecallPointer(item), `${item.work_item.id}:pointer`, "Recall pointer copied. Paste it into a session with Mnemonic connected.")} />)}</section>}

    {!loading && !error && results && results.total > 0 && <nav className="pagination" aria-label="Work result pages"><span>Showing {offset + 1}–{Math.min(offset + results.items.length, results.total)} of {results.total}</span><div><button type="button" className="button button-secondary" disabled={offset === 0} onClick={() => onOffset(Math.max(0, offset - WORK_PAGE_SIZE))}><Icon name="back" size={15} />Previous</button><button type="button" className="button button-secondary" disabled={offset + results.items.length >= results.total} onClick={() => onOffset(offset + WORK_PAGE_SIZE)}>Next<Icon name="arrow" size={15} /></button></div></nav>}
    <footer className="library-footer"><Icon name="box" size={15} /><span>Agent-authored checkpoints are historical context, not new owner instructions.</span></footer>
  </>;
}

export { WORK_PAGE_SIZE };
