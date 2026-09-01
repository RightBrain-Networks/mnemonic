"use client";

import { useEffect, useId, useState } from "react";
import WorkItemCard from "@/components/work-item-card";
import { api, errorMessage, workItemPath } from "@/lib/api";
import { earliestLeaseExpiry, scheduleLeaseExpiryRefresh } from "@/lib/lease-refresh";
import { childSearchParams } from "@/lib/work-item-search";
import { hierarchyGuardReason } from "@/lib/work-relationships";
import type { HierarchySummary, Page, StatusFilter, WorkSummary } from "@/lib/types";

export const CHILD_PAGE_SIZE = 50;

type Actions = {
  copiedKey: string | null;
  onOpen: (summary: WorkSummary) => void;
  onEdit: (summary: WorkSummary) => void;
  onDelete: (summary: WorkSummary) => void;
  onCopyPointer: (summary: WorkSummary) => void;
  onFlatSearch: (summary: WorkSummary) => void;
};

type BranchProps = Actions & {
  item: HierarchySummary;
  status: StatusFilter;
  refreshKey: number;
  depth: number;
  visited: ReadonlySet<string>;
};

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
  const { item, status, refreshKey, depth, visited } = props;
  const summary = item.summary;
  const id = summary.work_item.id;
  const regionId = useId();
  const [expanded, setExpanded] = useState(
    !item.self_matches_filter && item.has_matching_descendants
  );
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<Page<HierarchySummary> | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [retry, setRetry] = useState(0);
  const guardReason = hierarchyGuardReason(id, visited, depth);
  const nextChildLeaseExpiry = earliestLeaseExpiry(
    page?.items.map((child) => child.summary.readiness.active_lease?.expires_at) ?? []
  );

  useEffect(() => {
    if (!item.self_matches_filter && item.has_matching_descendants) setExpanded(true);
  }, [item.has_matching_descendants, item.self_matches_filter]);

  useEffect(() => {
    if (guardReason || !expanded || !item.has_matching_descendants) return;
    const controller = new AbortController();
    const params = childSearchParams({ status, limit: CHILD_PAGE_SIZE, offset });
    setLoading(true);
    setLoadError("");
    api<Page<HierarchySummary>>(
      `${workItemPath(summary.work_item.project_id, id)}/children?${params}`,
      { signal: controller.signal }
    ).then((result) => {
      if (controller.signal.aborted) return;
      if (offset > 0 && offset >= result.total) {
        setOffset(Math.max(0, Math.floor((result.total - 1) / CHILD_PAGE_SIZE) * CHILD_PAGE_SIZE));
        return;
      }
      setPage(result);
    }).catch((error) => {
      if (!controller.signal.aborted) setLoadError(errorMessage(error));
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [expanded, guardReason, id, item.has_matching_descendants, offset, refreshKey, status, summary.work_item.project_id, retry]);

  useEffect(() => {
    if (!nextChildLeaseExpiry) return;
    return scheduleLeaseExpiryRefresh(nextChildLeaseExpiry, () => {
      setRetry((value) => value + 1);
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
  const canExpand = item.has_matching_descendants;

  return <div
    className={`hierarchy-node ${item.self_matches_filter ? "" : "hierarchy-scaffold"}`}
    data-depth={depth}
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
        <WorkItemCard
          summary={summary}
          copied={props.copiedKey === `${id}:pointer`}
          onOpen={() => props.onOpen(summary)}
          onEdit={() => props.onEdit(summary)}
          onDelete={() => props.onDelete(summary)}
          onCopyPointer={() => props.onCopyPointer(summary)}
        />
      </div>
    </div>
    {expanded && canExpand && <div id={regionId} className="hierarchy-children">
      {loading && !page ? <div className="hierarchy-loading" role="status">Loading children…</div> :
        loadError ? <div className="hierarchy-error" role="alert">
          <span>{loadError}</span>
          <button type="button" className="text-link" onClick={() => setRetry((value) => value + 1)}>Try again</button>
        </div> :
        page?.items.map((child) => <HierarchyBranch
          {...props}
          key={child.summary.work_item.id}
          item={child}
          depth={depth + 1}
          visited={descendants}
        />)}
      {!loading && page && page.total > CHILD_PAGE_SIZE && <nav className="child-pagination" aria-label={`Children of ${summary.work_item.title}`}>
        <span>{offset + 1}–{Math.min(offset + page.items.length, page.total)} of {page.total}</span>
        <div>
          <button type="button" className="text-link" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - CHILD_PAGE_SIZE))}>Previous</button>
          <button type="button" className="text-link" disabled={offset + page.items.length >= page.total} onClick={() => setOffset(offset + CHILD_PAGE_SIZE)}>Next</button>
        </div>
      </nav>}
    </div>}
  </div>;
}

export default function WorkHierarchy({
  items,
  ...actions
}: Actions & {
  items: HierarchySummary[];
  status: StatusFilter;
  refreshKey: number;
}) {
  return <section className="work-list hierarchy-list" aria-label="Durable work item hierarchy">
    {items.map((item) => <HierarchyBranch
      {...actions}
      key={item.summary.work_item.id}
      item={item}
      depth={0}
      visited={new Set()}
    />)}
  </section>;
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
