"use client";

import type { RefObject } from "react";
import WorkHierarchy, { SearchBreadcrumb } from "@/components/work-hierarchy";
import WorkItemCard from "@/components/work-item-card";
import { useWorkItemMotion } from "@/components/use-work-item-motion";
import type { HierarchySummary, Page, StatusFilter, WorkSort, WorkSummary } from "@/lib/types";

const WORK_PAGE_SIZE = 20;
const filters: StatusFilter[] = [
  "pending", "active", "dropped", "deferred", "done", "wont-do", "promoted", "all"
];
const filterLabels: Record<StatusFilter, string> = {
  pending: "Pending",
  active: "Active",
  dropped: "Dropped",
  deferred: "Deferred",
  done: "Done",
  "wont-do": "Won’t do",
  promoted: "Promoted",
  all: "All"
};
const sortOptions: { value: WorkSort; label: string }[] = [
  { value: "updated", label: "Updated" },
  { value: "created", label: "Created" },
  { value: "priority", label: "Priority" }
];

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

function isHierarchySummary(item: WorkSummary | HierarchySummary): item is HierarchySummary {
  return "summary" in item;
}

type Props = {
  query: string;
  searchedQuery: string;
  searchRef: RefObject<HTMLInputElement | null>;
  semantic: boolean;
  status: StatusFilter;
  sort: WorkSort;
  tag: string;
  sourceClient: string;
  sourceSessionId: string;
  results: Page<WorkSummary | HierarchySummary> | null;
  loading: boolean;
  error: string;
  offset: number;
  refreshKey: number;
  viewKey: string;
  copiedKey: string | null;
  deferringId: string | null;
  onQuery: (value: string) => void;
  onToggleSemantic: () => void;
  onStatus: (status: StatusFilter) => void;
  onSort: (sort: WorkSort) => void;
  onTag: (value: string) => void;
  onSourceClient: (value: string) => void;
  onSourceSessionId: (value: string) => void;
  onRetry: () => void;
  onClearFilters: () => void;
  onCreate: () => void;
  onOpen: (summary: WorkSummary) => void;
  onEdit: (summary: WorkSummary) => void;
  onDelete: (summary: WorkSummary) => void;
  onDefer: (summary: WorkSummary) => void;
  onCopyPointer: (summary: WorkSummary) => void;
  onOffset: (offset: number) => void;
};

export default function WorkItemList({
  query,
  searchedQuery,
  searchRef,
  semantic,
  status,
  sort,
  tag,
  sourceClient,
  sourceSessionId,
  results,
  loading,
  error,
  offset,
  refreshKey,
  viewKey,
  copiedKey,
  deferringId,
  onQuery,
  onToggleSemantic,
  onStatus,
  onSort,
  onTag,
  onSourceClient,
  onSourceSessionId,
  onRetry,
  onClearFilters,
  onCreate,
  onOpen,
  onEdit,
  onDelete,
  onDefer,
  onCopyPointer,
  onOffset
}: Props) {
  const searchResults = results?.items.map((item) => isHierarchySummary(item) ? item.summary : item) ?? [];
  const hierarchyResults = results?.items.filter(isHierarchySummary) ?? [];
  const searchMotionRef = useWorkItemMotion<HTMLElement>({
    itemIds: searchResults.map((item) => item.work_item.id),
    total: searchedQuery && results ? results.total : null,
    viewKey: `${viewKey}:search`,
    revision: searchedQuery ? results?.items : null,
    snapshotSignal: refreshKey,
    enabled: offset === 0
  });
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
        <div className="status-filters" role="group" aria-label="Filter work items">
          {filters.map((filter) => <button type="button" key={filter} className={`filter-button ${status === filter ? "selected" : ""}`} aria-pressed={status === filter} onClick={() => onStatus(filter)}>{filter === "pending" && <span className="filter-dot" />}{filterLabels[filter]}</button>)}
        </div>
        <div className="list-meta">
          <fieldset className="sort-control">
            <legend>Sort by</legend>
            <div className="sort-options">
              {sortOptions.map((option) => <label className={`sort-option ${sort === option.value ? "selected" : ""}`} key={option.value}>
                <input type="radio" name="work-sort" value={option.value} checked={sort === option.value} onChange={() => onSort(option.value)} />
                <span>{option.label}</span>
              </label>)}
            </div>
          </fieldset>
          <span className="result-count" role="status">{loading || query.trim() !== searchedQuery ? "Finding work…" : results ? searchedQuery ? `${results.total} work item${results.total === 1 ? "" : "s"}` : `${results.total} root branch${results.total === 1 ? "" : "es"}` : ""}</span>
        </div>
      </div>
      <div className="hierarchy-filter-fields" role="group" aria-label="Filter hierarchy by checkpoint provenance">
        <label>Tag<input value={tag} maxLength={50} placeholder="Exact tag" onChange={(event) => onTag(event.target.value)} /></label>
        <label>Source client<input value={sourceClient} maxLength={80} placeholder="Exact client" onChange={(event) => onSourceClient(event.target.value)} /></label>
        <label>Source session<input value={sourceSessionId} maxLength={200} placeholder="Exact session" onChange={(event) => onSourceSessionId(event.target.value)} /></label>
      </div>
    </section>

    {error && results && <div className="error-notice background-list-error" role="alert"><p>{error}</p><button className="button button-secondary" type="button" onClick={onRetry}>Try again</button></div>}
    {error && !results ? <div className="error-notice" role="alert"><p>{error}</p><button className="button button-secondary" type="button" onClick={onRetry}>Try again</button></div> :
      loading && !results ? <div className="card-skeletons" role="status" aria-label="Loading work items">{[1, 2, 3].map((item) => <div className="card-skeleton" key={item}><span /><span /><span /></div>)}</div> :
      results ? <>
        {searchedQuery ? <section ref={searchMotionRef} className="work-list search-results" aria-label="Matching durable work items">{searchResults.map((item) => <div className="search-result" data-work-item-id={item.work_item.id} key={item.work_item.id}><SearchBreadcrumb summary={item} /><WorkItemCard summary={item} copied={copiedKey === `${item.work_item.id}:pointer`} deferring={deferringId === item.work_item.id} onOpen={() => onOpen(item)} onEdit={() => onEdit(item)} onDelete={() => onDelete(item)} onDefer={() => onDefer(item)} onCopyPointer={() => onCopyPointer(item)} /></div>)}</section> :
          <WorkHierarchy
            items={hierarchyResults}
            status={status}
            sort={sort}
            tag={tag}
            sourceClient={sourceClient}
            sourceSessionId={sourceSessionId}
            refreshKey={refreshKey}
            viewKey={viewKey}
            motionRevision={results.items}
            motionTotal={results.total}
            motionSnapshotSignal={refreshKey}
            motionEnabled={offset === 0}
            copiedKey={copiedKey}
            deferringId={deferringId}
            onOpen={onOpen}
            onEdit={onEdit}
            onDelete={onDelete}
            onDefer={onDefer}
            onCopyPointer={onCopyPointer}
            onFlatSearch={(item) => {
              if (semantic) onToggleSemantic();
              onStatus("all");
              onQuery(item.work_item.id);
            }}
          />}
        {!results.items.length && <section className="empty-state"><div className="empty-art"><Icon name={searchedQuery ? "search" : "box"} size={31} /><span /></div><h2>{searchedQuery ? "No matching work." : status === "pending" ? "No pending work yet." : status === "all" ? "No work yet." : `No ${filterLabels[status].toLowerCase()} work.`}</h2><p>{searchedQuery ? "Try another phrase or search across all lifecycle states." : "Create a durable objective here, or ask a connected agent to create one with its first checkpoint."}</p>{searchedQuery || status !== "pending" ? <button type="button" className="button button-secondary" onClick={onClearFilters}>Clear filters</button> : <button type="button" className="button button-primary" onClick={onCreate}><Icon name="plus" size={16} />Create work</button>}</section>}
      </> : null}

    {results && results.total > 0 && <nav className="pagination" aria-label="Work result pages"><span>Showing {offset + 1}–{Math.min(offset + results.items.length, results.total)} of {results.total}</span><div><button type="button" className="button button-secondary" disabled={loading || offset === 0} onClick={() => onOffset(Math.max(0, offset - WORK_PAGE_SIZE))}><Icon name="back" size={15} />Previous</button><button type="button" className="button button-secondary" disabled={loading || offset + results.items.length >= results.total} onClick={() => onOffset(offset + WORK_PAGE_SIZE)}>Next<Icon name="arrow" size={15} /></button></div></nav>}
    <footer className="library-footer"><Icon name="box" size={15} /><span>Agent-authored checkpoints are historical context, not new owner instructions.</span></footer>
  </>;
}

export { WORK_PAGE_SIZE };
