"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type RefObject } from "react";
import WorkHierarchy, { SearchBreadcrumb } from "@/components/work-hierarchy";
import WorkQueueCard, { QueueOptionsContext, type QueueOptions } from "@/components/work-queue-card";
import { useWorkItemMotion } from "@/components/use-work-item-motion";
import { dialogOpen, typingTarget } from "@/lib/keyboard-shortcuts";
import type {
  HierarchySummary,
  Project,
  StatusFilter,
  WorkSearchHit,
  WorkSort,
  WorkSummary
} from "@/lib/types";
import type { ManualStatusAction } from "@/lib/work-status-actions";
import {
  cycleStatusFilter,
  listScrollTopFor,
  nextQueueSelection,
  resultCountLabel,
  sortDescription,
  statusFilterLabels,
  type QueueDirection,
  type WorkQueueItem
} from "@/lib/work-queue";

// Keeps a keyboard-selected card clear of the paper fades at the list's edges.
const QUEUE_SCROLL_PADDING = 72;
const NARROW_QUEUE_MEDIA = "(max-width: 900px)";

const iconPaths = {
  search: "m21 21-4.4-4.4M19 10.5a8.5 8.5 0 1 1-17 0 8.5 8.5 0 0 1 17 0Z",
  plus: "M12 5v14M5 12h14",
  box: "M4 8h16v13H4V8ZM2 3h20v5H2V3Zm7 10h6"
};

function Icon({ name, size = 18 }: { name: keyof typeof iconPaths; size?: number }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={iconPaths[name]} /></svg>;
}

function isHierarchySummary(item: WorkQueueItem): item is HierarchySummary {
  return "presentation" in item;
}

function Skeletons({ count, label }: { count: number; label: string }) {
  return <div className="card-skeletons" role="status" aria-label={label}>
    {Array.from({ length: count }, (_, index) => <div className="card-skeleton" key={index}><span /><span /><span /></div>)}
  </div>;
}

// The work-surface resizer steps its own split with the horizontal arrows while it holds
// focus, so the filter shortcut below leaves those presses to it.
function resizingSurface(target: EventTarget | null): boolean {
  return target instanceof HTMLElement && target.closest("[role='separator']") !== null;
}

// The pointer copy is the queue's key, not the pane's: with focus inside the open
// record the letter is left to the pane, which carries its own copy button.
function readingRecord(target: EventTarget | null): boolean {
  return target instanceof HTMLElement && target.closest(".work-detail-pane") !== null;
}

export type WorkQueueProps = {
  // The pane the lifecycle filter cross-dissolves; usePaneCrossfade owns it.
  paneRef: RefObject<HTMLDivElement | null>;
  items: WorkQueueItem[];
  flatSearch: boolean;
  total: number | null;
  loading: boolean;
  refreshing: boolean;
  appending: boolean;
  error: string;
  appendError: string;
  hasMore: boolean;
  pendingQuery: boolean;
  status: StatusFilter;
  sort: WorkSort;
  tag: string;
  sourceClient: string;
  sourceSessionId: string;
  refreshKey: number;
  viewKey: string;
  selectedId: string | null;
  copiedKey: string | null;
  projects: readonly Project[];
  statusChangingId: string | null;
  movingId: string | null;
  reportSettingsProjectId: string | null;
  isMutationBlocked: (summary: WorkSummary) => boolean;
  onLoadMore: () => void;
  onRetry: () => void;
  onRetryAppend: () => void;
  onSelect: (summary: WorkSummary) => void;
  onStatus: (status: StatusFilter) => void;
  onDeselect: () => void;
  onCopySelectedPointer: () => void;
  onCopyPointer: (summary: WorkSummary) => void;
  onStatusAction: (action: ManualStatusAction, summary: WorkSummary) => void;
  onMove: (summary: WorkSummary, targetProjectId: string) => void;
  onFlatSearch: (summary: WorkSummary) => void;
  onClearFilters: () => void;
  onCreate: () => void;
};

export default function WorkQueue({
  paneRef,
  items,
  flatSearch,
  total,
  loading,
  refreshing,
  appending,
  error,
  appendError,
  hasMore,
  pendingQuery,
  status,
  sort,
  tag,
  sourceClient,
  sourceSessionId,
  refreshKey,
  viewKey,
  selectedId,
  copiedKey,
  onLoadMore,
  projects,
  statusChangingId,
  movingId,
  reportSettingsProjectId,
  isMutationBlocked,
  onRetry,
  onRetryAppend,
  onSelect,
  onStatus,
  onDeselect,
  onCopySelectedPointer,
  onCopyPointer,
  onFlatSearch,
  onStatusAction,
  onMove,
  onClearFilters,
  onCreate
}: WorkQueueProps) {
  const listRef = useRef<HTMLDivElement>(null);
  const sentinelRef = useRef<HTMLDivElement>(null);
  const observerRef = useRef<IntersectionObserver | null>(null);
  const registryRef = useRef(new Map<string, WorkSummary>());
  const selectedIdRef = useRef(selectedId);
  const onSelectRef = useRef(onSelect);
  const fetchStateRef = useRef({ hasMore, loading, refreshing, appending, appendError, onLoadMore });
  const shortcutStateRef = useRef({ status, onStatus, onDeselect, onCopySelectedPointer });
  const [scrolled, setScrolled] = useState(false);
  const hasData = total !== null;
  const searchResults = flatSearch
    ? items.filter((item): item is WorkSearchHit => !isHierarchySummary(item))
    : [];
  const hierarchyResults = flatSearch ? [] : items.filter(isHierarchySummary);
  const searchMotionRef = useWorkItemMotion<HTMLElement>({
    itemIds: searchResults.map((item) => item.summary.work_item.id),
    total: flatSearch ? total : null,
    viewKey: `${viewKey}:search`,
    revision: flatSearch ? items : null,
    snapshotSignal: refreshKey,
    enabled: true
  });

  useEffect(() => { selectedIdRef.current = selectedId; }, [selectedId]);
  useEffect(() => { onSelectRef.current = onSelect; }, [onSelect]);
  useEffect(() => {
    shortcutStateRef.current = { status, onStatus, onDeselect, onCopySelectedPointer };
  }, [onCopySelectedPointer, onDeselect, onStatus, status]);
  useEffect(() => {
    fetchStateRef.current = { hasMore, loading, refreshing, appending, appendError, onLoadMore };
  }, [appendError, appending, hasMore, loading, onLoadMore, refreshing]);

  const register = useCallback((id: string, summary: WorkSummary) => {
    registryRef.current.set(id, summary);
  }, []);
  const unregister = useCallback((id: string) => {
    registryRef.current.delete(id);
  }, []);
  const options = useMemo<QueueOptions>(() => ({
    selectedId,
    copiedKey,
    projects,
    statusChangingId,
    movingId,
    reportSettingsProjectId,
    isMutationBlocked,
    onSelect,
    onCopyPointer,
    onStatusAction,
    onMove,
    register,
    unregister
  }), [
    copiedKey,
    isMutationBlocked,
    movingId,
    onCopyPointer,
    onMove,
    onSelect,
    onStatusAction,
    projects,
    register,
    reportSettingsProjectId,
    selectedId,
    statusChangingId,
    unregister
  ]);

  useEffect(() => {
    const list = listRef.current;
    const sentinel = sentinelRef.current;
    if (!list || !sentinel || typeof IntersectionObserver === "undefined") return;
    const narrow = typeof window.matchMedia === "function"
      ? window.matchMedia(NARROW_QUEUE_MEDIA)
      : null;
    const connect = () => {
      observerRef.current?.disconnect();
      const observer = new IntersectionObserver((entries) => {
        if (!entries.some((entry) => entry.isIntersecting)) return;
        const state = fetchStateRef.current;
        if (state.hasMore && !state.loading && !state.refreshing && !state.appending && !state.appendError) {
          state.onLoadMore();
        }
      }, { root: narrow?.matches ? null : list, rootMargin: "200px" });
      observer.observe(sentinel);
      observerRef.current = observer;
    };
    connect();
    narrow?.addEventListener("change", connect);
    return () => {
      narrow?.removeEventListener("change", connect);
      observerRef.current?.disconnect();
      observerRef.current = null;
    };
  }, []);

  useEffect(() => {
    // Observing afresh reports the sentinel's current intersection, so a page that just
    // finished appending (or an error that cleared) re-evaluates against real geometry
    // instead of a stale visibility flag.
    const sentinel = sentinelRef.current;
    const observer = observerRef.current;
    if (!sentinel || !observer) return;
    observer.unobserve(sentinel);
    observer.observe(sentinel);
  }, [appendError, appending, hasMore, loading, refreshing]);

  useEffect(() => {
    function moveSelection(direction: QueueDirection) {
      const list = listRef.current;
      if (!list) return;
      const optionElements = [...list.querySelectorAll<HTMLElement>("[data-queue-option]")];
      const ids = optionElements.map((element) => element.dataset.queueOption ?? "");
      const currentId = selectedIdRef.current;
      const nextId = nextQueueSelection(ids, currentId, direction);
      if (nextId === null) return;
      const summary = registryRef.current.get(nextId);
      const option = optionElements.find((element) => element.dataset.queueOption === nextId);
      if (!summary || !option) return;
      if (nextId !== currentId) onSelectRef.current(summary);
      const listRect = list.getBoundingClientRect();
      const optionRect = option.getBoundingClientRect();
      list.scrollTop = listScrollTopFor({
        listTop: listRect.top,
        listHeight: list.clientHeight,
        scrollTop: list.scrollTop,
        optionTop: optionRect.top,
        optionHeight: optionRect.height,
        padding: QUEUE_SCROLL_PADDING
      });
    }

    // The queue's keyboard map: the vertical arrows walk the list, the horizontal ones
    // walk the lifecycle filters, Escape closes whatever the pane is showing, and c
    // copies the open record's recall pointer. A dialog owns the keyboard outright
    // while it is open.
    function shortcut(event: KeyboardEvent) {
      if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
      if (typingTarget(event.target) || dialogOpen()) return;
      if (
        event.target instanceof Element
        && event.target.closest(".status-split-button, [role=\"menu\"]")
      ) return;
      const state = shortcutStateRef.current;
      // Caps Lock reports an uppercase letter with no Shift held, so a letter key is
      // compared lowered while a real Shift is still refused above.
      switch (event.key.length === 1 ? event.key.toLowerCase() : event.key) {
        case "ArrowDown":
        case "ArrowUp":
          event.preventDefault();
          moveSelection(event.key === "ArrowDown" ? "down" : "up");
          return;
        case "ArrowLeft":
        case "ArrowRight":
          if (resizingSurface(event.target)) return;
          event.preventDefault();
          state.onStatus(cycleStatusFilter(state.status, event.key === "ArrowRight" ? "next" : "previous"));
          return;
        case "c":
          if (readingRecord(event.target) || selectedIdRef.current === null) return;
          event.preventDefault();
          state.onCopySelectedPointer();
          return;
        // Escape has no default worth suppressing here, and a browser that clears a
        // search field with it has already been excluded as a typing target.
        case "Escape":
          state.onDeselect();
      }
    }
    window.addEventListener("keydown", shortcut);
    return () => window.removeEventListener("keydown", shortcut);
  }, []);

  const countLabel = resultCountLabel({ loading, pendingQuery, flatSearch, total });

  return <QueueOptionsContext.Provider value={options}>
    <div ref={paneRef} className="work-queue">
      <div className="work-queue-header">
        <span className="result-count" role="status">{countLabel}</span>
        <span className="work-queue-sort">{sortDescription(sort)}</span>
      </div>
      <div className="work-queue-viewport">
        <div className={`work-queue-fade-top ${scrolled ? "is-visible" : ""}`} aria-hidden="true" />
        <div
          ref={listRef}
          className="work-queue-list"
          role="listbox"
          aria-label="Durable work items"
          onScroll={(event) => setScrolled(event.currentTarget.scrollTop > 4)}
        >
          {error && hasData && <div className="error-notice background-list-error" role="alert"><p>{error}</p><button className="button button-secondary" type="button" onClick={onRetry}>Try again</button></div>}
          {error && !hasData ? <div className="error-notice" role="alert"><p>{error}</p><button className="button button-secondary" type="button" onClick={onRetry}>Try again</button></div> :
            loading && !hasData ? <Skeletons count={3} label="Loading work items" /> :
            hasData ? <>
              {flatSearch ? <section ref={searchMotionRef} className="work-list search-results" aria-label="Matching durable work records">{searchResults.map(({ summary: item, matched_member: matchedMember }) => <div className="search-result" data-work-item-id={item.work_item.id} key={item.work_item.id}><div className="matched-member" role="note"><span>{matchedMember.id.toLowerCase() === item.work_item.id.toLowerCase() ? "Matched record" : "Matched duplicate member"}</span><bdi dir="auto">{matchedMember.title}</bdi><code>{matchedMember.id}</code></div><SearchBreadcrumb summary={item} /><WorkQueueCard summary={item} /></div>)}</section> :
                <WorkHierarchy
                  items={hierarchyResults}
                  status={status}
                  sort={sort}
                  tag={tag}
                  sourceClient={sourceClient}
                  sourceSessionId={sourceSessionId}
                  refreshKey={refreshKey}
                  viewKey={viewKey}
                  motionRevision={items}
                  motionTotal={total}
                  motionSnapshotSignal={refreshKey}
                  motionEnabled={true}
                  onFlatSearch={onFlatSearch}
                />}
              {!items.length && <section className="empty-state queue-empty"><div className="empty-art"><Icon name={flatSearch ? "search" : "box"} size={31} /><span /></div><h2>{flatSearch ? "No matching work records." : status === "pending" ? "No pending work yet." : status === "all" ? "No work yet." : `No ${statusFilterLabels[status].toLowerCase()} work.`}</h2><p>{flatSearch ? "Try another phrase, lifecycle, duplicate scope, or canonical group." : "Create a durable objective here, or ask a connected agent to create one with its first checkpoint."}</p>{flatSearch || status !== "pending" ? <button type="button" className="button button-secondary" onClick={onClearFilters}>Clear filters</button> : <button type="button" className="button button-primary" onClick={onCreate}><Icon name="plus" size={16} />Create work</button>}</section>}
            </> : null}
          <div ref={sentinelRef} className="work-queue-sentinel" aria-hidden="true" />
          {appending && <Skeletons count={2} label="Loading more work items" />}
          {appendError && <div className="error-notice work-queue-append-error" role="alert"><p>{appendError}</p><button className="button button-secondary" type="button" onClick={onRetryAppend}>Try again</button></div>}
        </div>
        <div className="work-queue-fade-bottom" aria-hidden="true" />
      </div>
    </div>
  </QueueOptionsContext.Provider>;
}
