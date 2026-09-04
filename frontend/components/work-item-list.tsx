"use client";

import { useEffect, useState, type ReactNode, type RefObject } from "react";
import { useWorkSplit } from "@/components/use-work-split";
import WorkQueue from "@/components/work-queue";
import type { DuplicateScope, StatusFilter, WorkSort, WorkSummary } from "@/lib/types";
import {
  WORK_PAGE_SIZE,
  moreFiltersForced,
  statusFilterLabels,
  statusFilterOrder,
  type WorkQueueItem
} from "@/lib/work-queue";
import { WORK_SPLIT_MAX, WORK_SPLIT_MIN } from "@/lib/work-split";

const sortOptions: { value: WorkSort; label: string }[] = [
  { value: "updated", label: "Updated" },
  { value: "created", label: "Created" },
  { value: "priority", label: "Priority" }
];

const iconPaths = {
  search: "m21 21-4.4-4.4M19 10.5a8.5 8.5 0 1 1-17 0 8.5 8.5 0 0 1 17 0Z",
  close: "m6 6 12 12M6 18 18 6",
  box: "M4 8h16v13H4V8ZM2 3h20v5H2V3Zm7 10h6",
  filterLines: "M4 6h16M7 12h10M10 18h4"
};

function Icon({ name, size = 18 }: { name: keyof typeof iconPaths; size?: number }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={iconPaths[name]} /></svg>;
}

export type WorkItemListProps = {
  // The queue pane the lifecycle filter cross-dissolves; usePaneCrossfade owns it.
  queuePaneRef: RefObject<HTMLDivElement | null>;
  // Search and filter controls.
  query: string;
  searchedQuery: string;
  searchRef: RefObject<HTMLInputElement | null>;
  semantic: boolean;
  duplicateScope: DuplicateScope;
  canonicalWorkItemId: string;
  status: StatusFilter;
  sort: WorkSort;
  tag: string;
  sourceClient: string;
  sourceSessionId: string;
  // Accumulated queue pages from useWorkQueuePages.
  items: WorkQueueItem[];
  flatSearch: boolean;
  total: number | null;
  loading: boolean;
  refreshing: boolean;
  appending: boolean;
  error: string;
  appendError: string;
  hasMore: boolean;
  refreshKey: number;
  viewKey: string;
  // Selection and clipboard state.
  selectedId: string | null;
  copiedKey: string | null;
  onQuery: (value: string) => void;
  onToggleSemantic: () => void;
  onDuplicateScope: (scope: DuplicateScope) => void;
  onClearDuplicateGroup: () => void;
  onStatus: (status: StatusFilter) => void;
  onSort: (sort: WorkSort) => void;
  onTag: (value: string) => void;
  onSourceClient: (value: string) => void;
  onSourceSessionId: (value: string) => void;
  onRetry: () => void;
  onRetryAppend: () => void;
  onLoadMore: () => void;
  onClearFilters: () => void;
  onCreate: () => void;
  onSelect: (summary: WorkSummary) => void;
  onDeselect: () => void;
  onCopySelectedPointer: () => void;
  onCopyPointer: (summary: WorkSummary) => void;
  // The right column of the work surface (the detail pane).
  detail: ReactNode;
};

export default function WorkItemList({
  queuePaneRef,
  query,
  searchedQuery,
  searchRef,
  semantic,
  duplicateScope,
  canonicalWorkItemId,
  status,
  sort,
  tag,
  sourceClient,
  sourceSessionId,
  items,
  flatSearch,
  total,
  loading,
  refreshing,
  appending,
  error,
  appendError,
  hasMore,
  refreshKey,
  viewKey,
  selectedId,
  copiedKey,
  onQuery,
  onToggleSemantic,
  onDuplicateScope,
  onClearDuplicateGroup,
  onStatus,
  onSort,
  onTag,
  onSourceClient,
  onSourceSessionId,
  onRetry,
  onRetryAppend,
  onLoadMore,
  onClearFilters,
  onCreate,
  onSelect,
  onDeselect,
  onCopySelectedPointer,
  onCopyPointer,
  detail
}: WorkItemListProps) {
  const [moreFilters, setMoreFilters] = useState(false);
  const workSplit = useWorkSplit<HTMLElement>();
  const forced = moreFiltersForced({
    duplicateScope,
    canonicalWorkItemId,
    tag,
    sourceClient,
    sourceSessionId
  });

  useEffect(() => {
    if (forced) setMoreFilters(true);
  }, [forced]);

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
        <div className="status-filters" role="group" aria-label="Filter work items" aria-keyshortcuts="ArrowLeft ArrowRight">
          {statusFilterOrder.map((filter) => <button type="button" key={filter} className={`filter-button ${status === filter ? "selected" : ""}`} aria-pressed={status === filter} onClick={() => onStatus(filter)}>{filter === "pending" && <span className="filter-dot" />}{statusFilterLabels[filter]}</button>)}
        </div>
        <div className="filter-controls">
          <div className="sort-group">
            <span className="sort-label" aria-hidden="true">Sort</span>
            <fieldset className="sort-control">
              <legend className="sr-only">Sort by</legend>
              <div className="sort-options">
                {sortOptions.map((option) => <label className={`sort-option ${sort === option.value ? "selected" : ""}`} key={option.value}>
                  <input type="radio" name="work-sort" value={option.value} checked={sort === option.value} onChange={() => onSort(option.value)} />
                  <span>{option.label}</span>
                </label>)}
              </div>
            </fieldset>
          </div>
          <button
            type="button"
            className={`more-filters-toggle ${moreFilters ? "is-open" : ""}`}
            aria-expanded={moreFilters}
            aria-controls="more-filters-panel"
            onClick={() => setMoreFilters((value) => !value)}
          ><Icon name="filterLines" size={14} />More filters<span className="more-filters-chevron" aria-hidden="true">⌄</span></button>
        </div>
      </div>
      {moreFilters && <div className="more-filters-panel" id="more-filters-panel">
        <fieldset className="duplicate-scope-control">
          <legend>Duplicate records</legend>
          {(["canonical", "aliases", "all"] as DuplicateScope[]).map((scope) => <label key={scope} className={duplicateScope === scope ? "selected" : ""}>
            <input type="radio" name="duplicate-scope" checked={duplicateScope === scope} onChange={() => onDuplicateScope(scope)} />
            <span>{scope === "canonical" ? "Canonical only" : scope === "aliases" ? "Aliases only" : "All records"}</span>
          </label>)}
        </fieldset>
        {canonicalWorkItemId && <div className="duplicate-group-filter" role="status"><span>Canonical group</span><code>{canonicalWorkItemId}</code><button type="button" className="text-link" onClick={onClearDuplicateGroup}>Clear group</button></div>}
        <div className="hierarchy-filter-fields" role="group" aria-label="Filter hierarchy by checkpoint provenance">
          <label>Tag<input value={tag} maxLength={50} placeholder="Exact tag" onChange={(event) => onTag(event.target.value)} /></label>
          <label>Source client<input value={sourceClient} maxLength={80} placeholder="Exact client" onChange={(event) => onSourceClient(event.target.value)} /></label>
          <label>Source session<input value={sourceSessionId} maxLength={200} placeholder="Exact session" onChange={(event) => onSourceSessionId(event.target.value)} /></label>
        </div>
      </div>}
    </section>

    <section ref={workSplit.surfaceRef} className={`work-surface ${workSplit.resizing ? "is-resizing" : ""}`} style={workSplit.surfaceStyle} aria-label="Work surface">
      <WorkQueue
        paneRef={queuePaneRef}
        items={items}
        flatSearch={flatSearch}
        total={total}
        loading={loading}
        refreshing={refreshing}
        appending={appending}
        error={error}
        appendError={appendError}
        hasMore={hasMore}
        pendingQuery={query.trim() !== searchedQuery}
        status={status}
        sort={sort}
        tag={tag}
        sourceClient={sourceClient}
        sourceSessionId={sourceSessionId}
        refreshKey={refreshKey}
        viewKey={viewKey}
        selectedId={selectedId}
        copiedKey={copiedKey}
        onLoadMore={onLoadMore}
        onRetry={onRetry}
        onRetryAppend={onRetryAppend}
        onSelect={onSelect}
        onStatus={onStatus}
        onDeselect={onDeselect}
        onCopySelectedPointer={onCopySelectedPointer}
        onCopyPointer={onCopyPointer}
        onFlatSearch={(item) => {
          if (semantic) onToggleSemantic();
          onStatus("all");
          onQuery(item.work_item.id);
        }}
        onClearFilters={onClearFilters}
        onCreate={onCreate}
      />
      <div
        className="work-surface-resizer"
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize the work queue"
        aria-keyshortcuts="ArrowLeft ArrowRight Home End"
        aria-valuemin={WORK_SPLIT_MIN}
        aria-valuemax={WORK_SPLIT_MAX}
        aria-valuenow={Math.round(workSplit.split)}
        aria-valuetext={`Queue ${Math.round(workSplit.split)}% of the surface`}
        title="Drag to resize the queue. Double-click to reset."
        tabIndex={0}
        {...workSplit.separatorProps}
      ><span aria-hidden="true" /></div>
      {detail}
    </section>
    <footer className="library-footer"><Icon name="box" size={15} /><span>Agent-authored checkpoints are historical context, not new owner instructions.</span></footer>
  </>;
}

export { WORK_PAGE_SIZE };
