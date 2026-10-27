"use client";

import { useEffect, type ReactNode, type RefObject } from "react";
import { useWorkSplit } from "@/components/use-work-split";
import WorkQueue from "@/components/work-queue";
import type { Project, StatusFilter, WorkSort, WorkSummary } from "@/lib/types";
import type { ManualStatusAction } from "@/lib/work-status-actions";
import {
  WORK_PAGE_SIZE,
  statusFilterLabels,
  workItemStatusFilterOrder,
  type WorkQueueItem
} from "@/lib/work-queue";
import { TagBadge, useProjectTags } from "@/components/work-tags";
import type { TagMode } from "@/lib/tags";
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
  filterLines: "M4 6h16M7 12h10M10 18h4",
  chevronUp: "m6 15 6-6 6 6"
};

function Icon({ name, size = 18 }: { name: keyof typeof iconPaths; size?: number }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={iconPaths[name]} /></svg>;
}

export type WorkItemListProps = {
  projectId: string;
  selectedTags: string[];
  tagMode: TagMode;
  onTags: (tags: string[]) => void;
  onTagMode: (mode: TagMode) => void;
  pinnedWorkItemIds: readonly string[];
  onTogglePin: (id: string) => void;
  // The queue pane the lifecycle filter cross-dissolves; usePaneCrossfade owns it.
  queuePaneRef: RefObject<HTMLDivElement | null>;
  // Everything above the lifecycle filters, including the page heading.
  introductoryContent: ReactNode;
  libraryToolsOpen: boolean;
  onLibraryToolsOpen: (open: boolean) => void;
  query: string;
  searchedQuery: string;
  searchRef: RefObject<HTMLInputElement | null>;
  semantic: boolean;
  canonicalWorkItemId: string;
  status: StatusFilter;
  sort: WorkSort;
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
  projects: readonly Project[];
  statusChangingId: string | null;
  movingId: string | null;
  reportSettingsProjectId: string | null;
  isMutationBlocked: (summary: WorkSummary) => boolean;
  onQuery: (value: string) => void;
  onToggleSemantic: () => void;
  onClearDuplicateGroup: () => void;
  onStatus: (status: StatusFilter) => void;
  onSort: (sort: WorkSort) => void;
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
  onStatusAction: (action: ManualStatusAction, summary: WorkSummary) => void;
  onMove: (summary: WorkSummary, targetProjectId: string) => void;
  detail: ReactNode;
};

export default function WorkItemList({
  projectId, selectedTags, tagMode, onTags, onTagMode,
  pinnedWorkItemIds,
  onTogglePin,
  queuePaneRef,
  introductoryContent,
  libraryToolsOpen,
  onLibraryToolsOpen,
  query,
  searchedQuery,
  searchRef,
  semantic,
  canonicalWorkItemId,
  status,
  sort,
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
  projects,
  statusChangingId,
  movingId,
  reportSettingsProjectId,
  isMutationBlocked,
  onClearDuplicateGroup,
  onStatus,
  onSort,
  onRetry,
  onRetryAppend,
  onLoadMore,
  onClearFilters,
  onCreate,
  onSelect,
  onDeselect,
  onCopySelectedPointer,
  onCopyPointer,
  onStatusAction,
  onMove,
  detail
}: WorkItemListProps) {
  const workSplit = useWorkSplit<HTMLElement>();
  const catalog = useProjectTags(projectId);
  useEffect(() => {
    if (!catalog.data) return;
    const available = new Set(catalog.data.items.map((tag) => tag.name));
    const next = selectedTags.filter((name) => available.has(name));
    if (next.length === selectedTags.length) return;
    onTags(next);
    if (!next.length) onTagMode("all");
  }, [catalog.data, selectedTags, onTags, onTagMode]);

  return <>
    <section className="library-controls" aria-label="Find work items">
      <section className={`library-tools ${libraryToolsOpen ? "is-open" : ""}`} aria-label="Work items overview">
        <button
          className="library-tools-toggle"
          type="button"
          aria-label={`${libraryToolsOpen ? "Collapse" : "Expand"} work items overview`}
          aria-expanded={libraryToolsOpen}
          aria-controls="library-tools-panel"
          onClick={() => onLibraryToolsOpen(!libraryToolsOpen)}
        >
          <span className="library-tools-chevron" aria-hidden="true"><Icon name="chevronUp" size={15} /></span>
        </button>
        <div
          className="library-tools-region"
          id="library-tools-panel"
          aria-hidden={!libraryToolsOpen}
          inert={!libraryToolsOpen}
        >
          <div className="library-tools-clip"><div className="library-tools-content">
            {introductoryContent}
            <div className="search-field">
              <Icon name="search" size={20} />
              <input ref={searchRef} type="search" value={query} maxLength={1000} aria-label="Search work items" placeholder="Search objectives, checkpoints, or session IDs…" onChange={(event) => onQuery(event.target.value)} />
              {query ? <button className="icon-button" type="button" aria-label="Clear search" onClick={() => onQuery("")}><Icon name="close" size={16} /></button> : <kbd aria-hidden="true">/</kbd>}
              <span className="search-mode-divider" />
              <button className={`semantic-toggle ${semantic ? "selected" : ""}`} type="button" aria-label="Semantic search" aria-pressed={semantic} onClick={onToggleSemantic}><span className="semantic-switch"><span /></span><span>Semantic</span></button>
            </div>
          </div></div>
        </div>
      </section>
      <div className="filter-row">
        <div className="status-filters" role="group" aria-label="Filter work items" aria-keyshortcuts="ArrowLeft ArrowRight">
          {workItemStatusFilterOrder.map((filter) => <button type="button" key={filter} className={`filter-button ${status === filter ? "selected" : ""}`} aria-pressed={status === filter} onClick={() => onStatus(filter)}>{filter === "pending" && <span className="filter-dot" />}{statusFilterLabels[filter]}</button>)}
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
        </div>
      </div>
      {canonicalWorkItemId && <div className="duplicate-group-filter" role="status"><span>Canonical group</span><code>{canonicalWorkItemId}</code><button type="button" className="text-link" onClick={onClearDuplicateGroup}>Clear group</button></div>}
      <div className="tag-filter-row" role="group" aria-label="Filter by tags">
        <div className="sort-group"><span className="sort-label" aria-hidden="true">Filter:</span>
          <fieldset className="sort-control"><legend className="sr-only">Tag matching</legend><div className="sort-options">
            {(["all", "or", "and"] as TagMode[]).map((mode) => <label className={`sort-option ${tagMode === mode ? "selected" : ""}`} key={mode}>
              <input type="radio" name="tag-mode" value={mode} checked={tagMode === mode} onChange={() => { onTagMode(mode); if (mode === "all") onTags([]); }} /><span>{mode === "all" ? "All" : mode.toUpperCase()}</span>
            </label>)}
          </div></fieldset>
        </div>
        <div className="tag-filter-options">{catalog.data?.items.map((tag) => <button type="button" key={tag.id} className={`tag-filter-button ${selectedTags.includes(tag.name) ? "selected" : ""}`} aria-pressed={selectedTags.includes(tag.name)} title={tag.description} onClick={() => {
          const next = selectedTags.includes(tag.name) ? selectedTags.filter((name) => name !== tag.name) : [...selectedTags, tag.name];
          onTags(next); if (next.length && tagMode === "all") onTagMode("or"); if (!next.length) onTagMode("all");
        }}><TagBadge name={tag.name} tag={tag} /></button>)}</div>
        {catalog.isPending && <span role="status" className="field-hint">Loading tags…</span>}
        {catalog.isError && <button type="button" className="text-link" onClick={() => void catalog.refetch()}>Retry loading tags</button>}
      </div>
    </section>

    <section ref={workSplit.surfaceRef} className={`work-surface ${workSplit.resizing ? "is-resizing" : ""}`} style={workSplit.surfaceStyle} aria-label="Work surface">
      <WorkQueue
        pinnedWorkItemIds={pinnedWorkItemIds}
        onTogglePin={onTogglePin}
        paneRef={queuePaneRef}
        items={items}
        flatSearch={flatSearch}
        semantic={semantic}
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
        tag="" tags={selectedTags} tagMode={tagMode === "all" ? "or" : tagMode}
        sourceClient=""
        sourceSessionId=""
        refreshKey={refreshKey}
        viewKey={viewKey}
        selectedId={selectedId}
        copiedKey={copiedKey}
        onLoadMore={onLoadMore}
        onRetry={onRetry}
        onRetryAppend={onRetryAppend}
        projects={projects}
        statusChangingId={statusChangingId}
        movingId={movingId}
        reportSettingsProjectId={reportSettingsProjectId}
        isMutationBlocked={isMutationBlocked}
        onSelect={onSelect}
        onStatus={onStatus}
        onDeselect={onDeselect}
        onCopySelectedPointer={onCopySelectedPointer}
        onCopyPointer={onCopyPointer}
        onStatusAction={onStatusAction}
        onMove={onMove}
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
  </>;
}

export { WORK_PAGE_SIZE };
