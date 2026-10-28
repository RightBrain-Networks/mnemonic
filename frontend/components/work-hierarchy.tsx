"use client";

import { useEffect, useId, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { workChildrenOptions, workDetailKeys } from "@/lib/work-detail-queries";
import WorkQueueCard from "@/components/work-queue-card";
import { useWorkItemMotion } from "@/components/use-work-item-motion";
import { errorMessage } from "@/lib/api";
import { earliestLeaseExpiry, scheduleLeaseExpiryRefresh } from "@/lib/lease-refresh";
import { hierarchyGuardReason } from "@/lib/work-relationships";
import type { HierarchySummary, StatusFilter, WorkSort, WorkSummary } from "@/lib/types";

export const CHILD_PAGE_SIZE = 50;

type Actions = {
  pinnedWorkItemIds: readonly string[];
  onFlatSearch: (summary: WorkSummary) => void;
};

type BranchProps = Actions & {
  item: HierarchySummary;
  status: StatusFilter;
  sort: WorkSort;
  tag: string;
  tags?: string[];
  tagMode?: "or" | "and";
  sourceClient: string;
  sourceSessionId: string;
  refreshKey: number;
  viewKey: string;
  depth: number;
  visited: ReadonlySet<string>;
  overrideFilters: boolean;
};

function naturalList(values: string[]): string {
  if (values.length < 2) return values[0] ?? "current";
  if (values.length === 2) return `${values[0]} and ${values[1]}`;
  return `${values.slice(0, -1).join(", ")}, and ${values.at(-1)}`;
}

function GuardedBranch({
  item,
  reason,
  onFlatSearch
}: {
  item: HierarchySummary;
  reason: string;
  onFlatSearch: (summary: WorkSummary) => void;
}) {
  return <div className="hierarchy-guard" role="note">
    <strong>{item.summary.work_item.title}</strong>
    <span>{reason}</span>
    <button type="button" className="text-link" onClick={() => onFlatSearch(item.summary)}>
      Show in flat search
    </button>
  </div>;
}

function HierarchyBranch(props: BranchProps) {
  const { item, status, sort, refreshKey, depth, visited } = props;
  const summary = item.summary;
  const id = summary.work_item.id;
  const regionId = useId();
  const [expanded, setExpanded] = useState(
    !item.self_matches_filter && item.has_matching_descendants
  );
  const [offset, setOffset] = useState(0);
  const [showAllDescendants, setShowAllDescendants] = useState(false);
  const guardReason = hierarchyGuardReason(id, visited, depth);
  const allDescendants = props.overrideFilters || showAllDescendants;
  const activeFilterCategories = [
    ...(status !== "all" ? ["lifecycle"] : []),
    ...(props.sourceClient.trim() || props.sourceSessionId.trim() ? ["source"] : []),
    ...(props.tag.trim() || props.tags?.length ? ["tag"] : [])
  ];
  const childViewKey = `${props.viewKey}:children:${id}:${allDescendants ? "all" : status}:${sort}:${offset}`;
  const childrenEnabled = !guardReason && expanded && (item.has_matching_descendants || allDescendants);
  const options = workChildrenOptions(summary.work_item.project_id, id, {
    statusScope: "work_item", status: allDescendants ? "all" : status, sort,
    limit: CHILD_PAGE_SIZE, offset,
    tag: allDescendants ? undefined : props.tag,
    tags: allDescendants ? undefined : props.tags,
    tagMode: props.tagMode,
    sourceClient: allDescendants ? undefined : props.sourceClient,
    sourceSessionId: allDescendants ? undefined : props.sourceSessionId,
    pinnedWorkItemIds: props.pinnedWorkItemIds
  });
  const childrenQuery = useQuery({ ...options,
    queryKey: childrenEnabled ? options.queryKey : workDetailKeys.children(summary.work_item.project_id, id, "disabled"), enabled: childrenEnabled
  });
  const page = childrenEnabled ? childrenQuery.data ?? null : null;
  const loadError = childrenEnabled && childrenQuery.error ? errorMessage(childrenQuery.error) : "";
  const loading = childrenEnabled && childrenQuery.isFetching;
  const nextChildLeaseExpiry = earliestLeaseExpiry(
    page?.items.flatMap((child) => [
      child.summary.readiness.active_lease?.expires_at,
      child.presentation.next_active_descendant_lease_expires_at
    ]) ?? []
  );
  const childMotionRef = useWorkItemMotion<HTMLDivElement>({
    itemIds: page?.items.map((child) => child.summary.work_item.id) ?? [],
    total: page?.total ?? null,
    viewKey: childViewKey,
    revision: page?.items,
    snapshotSignal: refreshKey,
    enabled: expanded && offset === 0
  });
  const focusChildrenRegion = () => childMotionRef.current?.focus();

  useEffect(() => {
    if (!item.self_matches_filter && item.has_matching_descendants) setExpanded(true);
  }, [item.has_matching_descendants, item.self_matches_filter]);

  useEffect(() => {
    setShowAllDescendants(false);
    setOffset(0);
  }, [props.viewKey, status]);

  useEffect(() => {
    if (page && offset > 0 && offset >= page.total) {
      setOffset(Math.max(0, Math.floor((page.total - 1) / CHILD_PAGE_SIZE) * CHILD_PAGE_SIZE));
    }
  }, [page, offset]);

  useEffect(() => {
    if (!nextChildLeaseExpiry) return;
    return scheduleLeaseExpiryRefresh(nextChildLeaseExpiry, () => {
      void childrenQuery.refetch();
    });
  }, [nextChildLeaseExpiry]);

  if (guardReason) {
    return <GuardedBranch
      item={item}
      reason={guardReason}
      onFlatSearch={props.onFlatSearch}
    />;
  }

  const descendants = new Set(visited);
  descendants.add(id);
  const canExpand = item.presentation.direct_child_count > 0;

  return <div
    className={`hierarchy-node ${item.self_matches_filter ? "" : "hierarchy-scaffold"}`}
    data-depth={depth}
    data-work-item-id={id}
  >
    <div className="hierarchy-node-row">
      {canExpand ? <button
        type="button"
        className="hierarchy-toggle"
        aria-expanded={expanded}
        aria-controls={regionId}
        aria-label={`${expanded ? "Collapse" : "Expand"} children of ${summary.work_item.title}`}
        onClick={() => setExpanded((value) => !value)}
      ><span aria-hidden="true">›</span></button> : <span className="hierarchy-toggle-spacer" />}
      <div className="hierarchy-card">
        {!item.self_matches_filter && <span className="scaffold-label">Ancestor · does not match this filter</span>}
        <WorkQueueCard summary={summary} presentation={item.presentation} depth={depth} />
      </div>
    </div>
    {expanded && canExpand && <div
      ref={childMotionRef}
      id={regionId}
      className="hierarchy-children"
      tabIndex={-1}
    >
      {!item.has_matching_descendants && !allDescendants ? <div className="hierarchy-hidden-children" role="note">
        <span>The {naturalList(activeFilterCategories)} filter{activeFilterCategories.length === 1 ? "" : "s"} {activeFilterCategories.length === 1 ? "hides" : "hide"} this branch’s children.</span>
        <button type="button" className="text-link" onClick={() => {
          setShowAllDescendants(true);
          setOffset(0);
          focusChildrenRegion();
        }}>Show all descendants</button>
        <small>This branch only: lifecycle, source, and tag predicates will all be cleared.</small>
      </div> : !page && !loadError ? <div className="hierarchy-loading" role="status">Loading children…</div> :
        loadError && !page ? <div className="hierarchy-error" role="alert">
          <span>{loadError}</span>
          <button type="button" className="text-link" onClick={() => {
            void childrenQuery.refetch();
            focusChildrenRegion();
          }}>Try again</button>
        </div> :
        <>
          {loadError && <div className="hierarchy-error" role="alert">
            <span>{loadError}</span>
            <button type="button" className="text-link" onClick={() => {
              void childrenQuery.refetch();
              focusChildrenRegion();
            }}>Try again</button>
          </div>}
          {page?.items.map((child) => <HierarchyBranch
            {...props}
            key={child.summary.work_item.id}
            item={child}
            depth={depth + 1}
            visited={descendants}
            overrideFilters={allDescendants}
          />)}
        </>}
      {showAllDescendants && <p className="hierarchy-filter-override" role="status">Branch override active: showing all descendants with lifecycle, source, and tag filters cleared.</p>}
      {page && page.total > CHILD_PAGE_SIZE && <nav className="child-pagination" aria-label={`Children of ${summary.work_item.title}`}>
        <span>{offset + 1}–{Math.min(offset + page.items.length, page.total)} of {page.total}</span>
        <div>
          <button type="button" className="text-link" disabled={loading || offset === 0} onClick={() => setOffset(Math.max(0, offset - CHILD_PAGE_SIZE))}>Previous</button>
          <button type="button" className="text-link" disabled={loading || offset + page.items.length >= page.total} onClick={() => setOffset(offset + CHILD_PAGE_SIZE)}>Next</button>
        </div>
      </nav>}
    </div>}
  </div>;
}

export default function WorkHierarchyRoot(
  props: Omit<BranchProps, "depth" | "visited" | "overrideFilters">
) {
  // WorkQueue owns root-sequence motion, including adjacent review tasks.
  // Descendant pages retain their independent motion hooks in HierarchyBranch.
  return <HierarchyBranch {...props} depth={0} visited={new Set()} overrideFilters={false} />;
}

export function SearchBreadcrumb({ summary }: { summary: WorkSummary }) {
  if (!summary.ancestor_path.length && !summary.ancestor_path_truncated) return null;
  return <nav className="search-ancestry" aria-label={`Ancestry for ${summary.work_item.title}`}>
    {summary.ancestor_path_truncated && <span title="Earlier ancestors were omitted"><span aria-hidden="true">…</span><span className="sr-only">Earlier ancestors were omitted</span></span>}
    {summary.ancestor_path.map((ancestor) => <span key={ancestor.id}>
      <span aria-hidden="true">/</span>{ancestor.title}
    </span>)}
    <span><span aria-hidden="true">/</span>{summary.work_item.title}</span>
  </nav>;
}
