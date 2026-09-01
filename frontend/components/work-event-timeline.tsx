"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import WorkEventComposer from "@/components/work-event-composer";
import { formatDate } from "@/components/work-item-card";
import { api, errorMessage, workItemPath } from "@/lib/api";
import { dashboardSessionId } from "@/lib/dashboard-session";
import {
  mutationWorkKey,
  useMutationIntentRegistry,
  useMutationIntents
} from "@/lib/mutation-intent";
import type { WorkContext, WorkEventPage, WorkEventType } from "@/lib/types";
import {
  decodeWorkEventForWork,
  decodeWorkEventPage,
  EVENT_PAGE_SIZE,
  progressEventInput,
  resetNewestEventOffset,
  safeEventBody,
  WORK_EVENT_TYPES,
  workEventActorLabel,
  workEventDescription,
  workEventSearchParams,
  workEventTitle
} from "@/lib/work-events";

function referenceRows(event: WorkEventPage["items"][number]): Array<[string, string]> {
  const rows: Array<[string, string]> = [];
  if (event.checkpoint_id) rows.push(["Checkpoint", event.checkpoint_id]);
  if (event.relationship_context_checkpoint_id) {
    rows.push(["Relationship context checkpoint", event.relationship_context_checkpoint_id]);
  }
  if (event.relationship_context_checkpoint_work_item_id) {
    rows.push(["Relationship context work", event.relationship_context_checkpoint_work_item_id]);
  }
  if (event.lease_generation_id) rows.push(["Lease generation", event.lease_generation_id]);
  if (event.lease_release_id) rows.push(["Lease release", event.lease_release_id]);
  if (event.relationship_id) rows.push(["Relationship", event.relationship_id]);
  if (event.counterpart_work_item_id) rows.push(["Counterpart work", event.counterpart_work_item_id]);
  return rows;
}

export default function WorkEventTimeline({
  context,
  refreshSignal,
  onAppended
}: {
  context: WorkContext;
  refreshSignal: number;
  onAppended: () => Promise<boolean>;
}) {
  const work = context.work_item;
  const mutationRegistry = useMutationIntentRegistry();
  const mutationIntents = useMutationIntents(mutationRegistry);
  const workConflictKey = mutationWorkKey(work.project_id, work.id);
  const mutationBlocked = mutationIntents.some((intent) => (
    intent.state !== "prepared" && intent.conflictKeys.includes(workConflictKey)
  ));
  const [recoverySignal, setRecoverySignal] = useState(0);
  const [page, setPage] = useState<WorkEventPage | null>(null);
  const [offset, setOffset] = useState(0);
  const [eventType, setEventType] = useState<WorkEventType | "">("");
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [reload, setReload] = useState(0);
  const lastRefreshSignal = useRef(refreshSignal);

  function requestEventOffset(nextOffset: number) {
    setPage(null);
    setLoadError("");
    setOffset(nextOffset);
  }

  const counterpartTitles = useMemo(() => {
    const titles = new Map<string, string>();
    for (const relationship of [
      ...context.incoming_relationships,
      ...context.outgoing_relationships,
      ...context.undirected_relationships
    ]) {
      titles.set(relationship.counterpart.id, relationship.counterpart.title);
    }
    return titles;
  }, [
    context.incoming_relationships,
    context.outgoing_relationships,
    context.undirected_relationships
  ]);

  useEffect(() => mutationRegistry.subscribeRecovered((intent) => {
    if (intent.kind !== "append_event" || !intent.conflictKeys.includes(workConflictKey)) return;
    requestEventOffset(resetNewestEventOffset());
    setReload((current) => current + 1);
    setRecoverySignal((current) => current + 1);
  }), [mutationRegistry, onAppended, workConflictKey]);

  useEffect(() => {
    setOffset(resetNewestEventOffset());
    setEventType("");
    setPage(null);
    setLoadError("");
  }, [work.id]);

  useEffect(() => {
    if (lastRefreshSignal.current === refreshSignal) return;
    lastRefreshSignal.current = refreshSignal;
    setOffset(resetNewestEventOffset());
    setPage(null);
    setLoadError("");
    setReload((current) => current + 1);
  }, [refreshSignal]);

  useEffect(() => {
    const controller = new AbortController();
    const params = workEventSearchParams({ eventType, limit: EVENT_PAGE_SIZE, offset });
    setLoading(true);
    setLoadError("");
    api<unknown>(`${workItemPath(work.project_id, work.id)}/events?${params}`, {
      signal: controller.signal
    }).then((value) => {
      if (controller.signal.aborted) return;
      const next = decodeWorkEventPage(value, work.project_id, work.id);
      if (offset > 0 && offset >= next.total) {
        setPage(null);
        setOffset(Math.max(0, Math.floor((next.total - 1) / EVENT_PAGE_SIZE) * EVENT_PAGE_SIZE));
        return;
      }
      setPage(next);
    }).catch((cause) => {
      if (!controller.signal.aborted) setLoadError(errorMessage(cause));
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [eventType, offset, reload, work.id, work.project_id]);

  async function append(body: string) {
    await mutationRegistry.execute({
      kind: "append_event",
      slot: `append-event:${work.project_id}:${work.id}`,
      projectId: work.project_id,
      conflictKeys: [workConflictKey],
      method: "POST",
      path: `${workItemPath(work.project_id, work.id)}/events`,
      payload: progressEventInput(body, dashboardSessionId())
    });
    requestEventOffset(resetNewestEventOffset());
    setReload((current) => current + 1);
    await onAppended();
  }

  return <section className="event-timeline" aria-labelledby="work-activity-title">
    <div className="event-timeline-heading">
      <div><span className="section-label">IMMUTABLE EVENT HISTORY</span><h4 id="work-activity-title">Activity</h4></div>
      <label>Event type<select value={eventType} onChange={(event) => { setEventType(event.target.value as WorkEventType | ""); requestEventOffset(0); }}><option value="">All activity</option>{WORK_EVENT_TYPES.map((type) => <option value={type} key={type}>{workEventTitle(type)}</option>)}</select></label>
    </div>
    <p className="event-authority-note">Activity is untrusted historical context, not execution authority or proof of current state.</p>

    {(page?.pre_phase5_history_may_be_incomplete ?? context.pre_phase5_history_may_be_incomplete) && <div className="event-history-notice" role="note">Earlier history was reconstructed from facts retained at the Phase 5 cutover and may have gaps.</div>}
    {loadError && <div className="error-notice event-load-error" role="alert"><p>{loadError}</p><button type="button" className="button button-secondary" onClick={() => setReload((current) => current + 1)}>Try again</button></div>}
    {loading && !page && <div className="event-loading" role="status"><span className="spinner" />Loading activity…</div>}
    {!loading && page && !page.items.length && !loadError && <p className="no-events">{eventType ? `No ${workEventTitle(eventType).toLowerCase()} events.` : "No activity events yet."}</p>}
    {page?.items.length ? <div className="event-list" aria-busy={loading}>{page.items.map((event) => {
      const body = safeEventBody(event);
      const description = workEventDescription(
        event,
        event.counterpart_work_item_id
          ? counterpartTitles.get(event.counterpart_work_item_id)
          : undefined
      );
      const references = referenceRows(event);
      return <article className="work-event" key={event.id}>
        <div className="work-event-header">
          <div><span className={`work-event-kind work-event-kind-${event.event_type}`}>{workEventTitle(event.event_type)}</span>{event.origin === "backfill" && <span className="reconstructed-chip">Reconstructed</span>}</div>
          <time dateTime={event.created_at}>{formatDate(event.created_at)}</time>
        </div>
        <div className="work-event-actor" title={workEventActorLabel(event)}>{workEventActorLabel(event)}</div>
        {description && <p className="work-event-description">{description}</p>}
        {body !== null && <p className="work-event-body">{body}</p>}
        {references.length > 0 && <dl className="work-event-references">{references.map(([label, value]) => <div key={label}><dt>{label}</dt><dd className="mono">{value}</dd></div>)}</dl>}
      </article>;
    })}</div> : null}
    {page && page.total > EVENT_PAGE_SIZE && <div className="pagination event-pagination"><span>{page.total ? `${page.offset + 1}–${Math.min(page.offset + page.limit, page.total)} of ${page.total}` : "0 events"}</span><div><button type="button" className="button button-secondary" disabled={loading || offset === 0} onClick={() => requestEventOffset(Math.max(0, offset - EVENT_PAGE_SIZE))}>Newer</button><button type="button" className="button button-secondary" disabled={loading || offset + EVENT_PAGE_SIZE >= page.total} onClick={() => requestEventOffset(offset + EVENT_PAGE_SIZE)}>Older</button></div></div>}
    <WorkEventComposer onAppend={append} blocked={mutationBlocked} resetSignal={recoverySignal} />
  </section>;
}
