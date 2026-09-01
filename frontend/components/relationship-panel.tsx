"use client";

import { useEffect, useMemo, useState } from "react";
import { OperationalBadge, StatusBadge, formatDate } from "@/components/work-item-card";
import { api, ApiError, errorMessage, workItemPath } from "@/lib/api";
import { dashboardSessionId } from "@/lib/dashboard-session";
import {
  mutationWorkKey,
  useMutationIntentRegistry,
  useMutationIntents
} from "@/lib/mutation-intent";
import {
  relationshipConflictMessage,
  relationshipGroup,
  relationshipPreview,
  relationshipTypeLabels
} from "@/lib/work-relationships";
import type {
  AdjacentRelationshipRead,
  Checkpoint,
  Page,
  RelationshipCreateInput,
  RelationshipCreationResult,
  RelationshipDirection,
  RelationshipRemovalResult,
  RelationshipType,
  WorkContext,
  WorkSummary
} from "@/lib/types";
import { workSearchParams } from "@/lib/work-item-search";
import { dashboardMutationActor } from "@/lib/work-events";

const groupOrder = [
  "Blocked by",
  "Blocks",
  "Parent",
  "Children",
  "Discovered from",
  "Discovered work",
  "Duplicate of",
  "Related"
];

type Props = {
  context: WorkContext;
  onChanged: () => Promise<boolean>;
};

type CheckpointChoice = {
  owner: string;
  checkpoint: Checkpoint;
};

export default function RelationshipPanel({ context, onChanged }: Props) {
  const work = context.work_item;
  const mutationRegistry = useMutationIntentRegistry();
  const mutationIntents = useMutationIntents(mutationRegistry);
  const [editorOpen, setEditorOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [searchedQuery, setSearchedQuery] = useState("");
  const [searchPage, setSearchPage] = useState<Page<WorkSummary> | null>(null);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState("");
  const [counterpart, setCounterpart] = useState<WorkSummary | null>(null);
  const [type, setType] = useState<RelationshipType>("related");
  const [direction, setDirection] = useState<Exclude<RelationshipDirection, "undirected">>("outgoing");
  const [checkpointChoices, setCheckpointChoices] = useState<CheckpointChoice[]>([]);
  const [checkpointId, setCheckpointId] = useState("");
  const [checkpointsLoading, setCheckpointsLoading] = useState(false);
  const [checkpointError, setCheckpointError] = useState("");
  const [saving, setSaving] = useState(false);
  const [checkpointRetry, setCheckpointRetry] = useState(0);
  const [removingId, setRemovingId] = useState("");
  const [actionError, setActionError] = useState("");
  const [actionNotice, setActionNotice] = useState("");

  const relationships = [
    ...context.incoming_relationships,
    ...context.outgoing_relationships,
    ...context.undirected_relationships
  ];
  const omittedRelationshipCount = Math.max(
    0,
    context.relationship_counts.total - relationships.length
  );
  const grouped = useMemo(() => {
    const groups = new Map<string, AdjacentRelationshipRead[]>();
    for (const relationship of relationships) {
      const label = relationshipGroup(relationship);
      groups.set(label, [...(groups.get(label) ?? []), relationship]);
    }
    return groups;
  }, [
    context.incoming_relationships,
    context.outgoing_relationships,
    context.undirected_relationships
  ]);

  useEffect(() => {
    const timer = setTimeout(() => setSearchedQuery(query.trim()), 250);
    return () => clearTimeout(timer);
  }, [query]);

  useEffect(() => {
    if (!editorOpen || !searchedQuery) {
      setSearchPage(null);
      setSearchError("");
      setSearching(false);
      return;
    }
    const controller = new AbortController();
    const params = workSearchParams({
      status: "all",
      sort: "updated",
      limit: 10,
      offset: 0,
      query: searchedQuery
    });
    setSearching(true);
    setSearchError("");
    api<Page<WorkSummary>>(`${workItemPath(work.project_id)}?${params}`, {
      signal: controller.signal
    }).then((page) => {
      if (!controller.signal.aborted) setSearchPage({
        ...page,
        items: page.items.filter((item) => item.work_item.id !== work.id)
      });
    }).catch((error) => {
      if (!controller.signal.aborted) setSearchError(errorMessage(error));
    }).finally(() => {
      if (!controller.signal.aborted) setSearching(false);
    });
    return () => controller.abort();
  }, [editorOpen, searchedQuery, work.id, work.project_id]);

  const counterpartId = counterpart?.work_item.id ?? "";
  const sourceId = direction === "outgoing" ? work.id : counterpartId;
  const targetId = direction === "outgoing" ? counterpartId : work.id;
  const workConflictKey = mutationWorkKey(work.project_id, work.id);
  const relationshipConflictKeys = [
    workConflictKey,
    ...(counterpartId ? [mutationWorkKey(work.project_id, counterpartId)] : [])
  ];
  const mutationBlocked = mutationIntents.some((intent) => (
    intent.state !== "prepared"
    && intent.conflictKeys.some((key) => relationshipConflictKeys.includes(key))
  ));

  useEffect(() => mutationRegistry.subscribeRecovered((intent) => {
    if (
      (intent.kind !== "add_relationship" && intent.kind !== "remove_relationship")
      || !intent.conflictKeys.includes(workConflictKey)
    ) return;
    if (intent.kind === "add_relationship") {
      setCounterpart(null);
      setQuery("");
      setSearchedQuery("");
      setCheckpointId("");
    }
    setActionError("");
    setActionNotice(
      intent.kind === "add_relationship"
        ? "The pending relationship request was recovered."
        : "The pending relationship removal was recovered."
    );
  }), [mutationRegistry, workConflictKey]);

  useEffect(() => {
    setCheckpointId("");
    setCheckpointChoices([]);
    setCheckpointError("");
    if (!editorOpen || !counterpart) {
      setCheckpointsLoading(false);
      return;
    }
    const endpointIds = type === "discovered-from"
      ? [targetId]
      : [work.id, counterpart.work_item.id];
    const uniqueIds = [...new Set(endpointIds)];
    const controller = new AbortController();
    setCheckpointsLoading(true);
    Promise.all(uniqueIds.map(async (id) => {
      const page = await api<Page<Checkpoint>>(
        `${workItemPath(work.project_id, id)}/checkpoints?order=newest&limit=100&offset=0`,
        { signal: controller.signal }
      );
      const owner = id === work.id ? work.title : counterpart.work_item.title;
      return page.items.map((checkpoint) => ({ owner, checkpoint }));
    })).then((choices) => {
      if (!controller.signal.aborted) setCheckpointChoices(choices.flat());
    }).catch((error) => {
      if (!controller.signal.aborted) setCheckpointError(errorMessage(error));
    }).finally(() => {
      if (!controller.signal.aborted) setCheckpointsLoading(false);
    });
    return () => controller.abort();
  }, [checkpointRetry, counterpart, direction, editorOpen, targetId, type, work.id, work.project_id, work.title]);

  async function addRelationship() {
    if (!counterpart || saving || removingId) return;
    if (type === "discovered-from" && !checkpointId) {
      setActionError("Choose the originating checkpoint on the relationship target.");
      return;
    }
    const payload: RelationshipCreateInput = {
      relationship_type: type,
      source_work_item_id: sourceId,
      target_work_item_id: targetId,
      created_by_client: "dashboard",
      created_by_session_id: dashboardSessionId(),
      created_by_model: null,
      ...(checkpointId ? { context_checkpoint_id: checkpointId } : {})
    };
    setSaving(true);
    setActionError("");
    setActionNotice("");
    try {
      const result = await mutationRegistry.execute({
        kind: "add_relationship",
        slot: `add-relationship:${work.project_id}:${sourceId}:${targetId}`,
        projectId: work.project_id,
        conflictKeys: [
          mutationWorkKey(work.project_id, sourceId),
          mutationWorkKey(work.project_id, targetId)
        ],
        method: "POST",
        path: `/projects/${encodeURIComponent(work.project_id)}/relationships`,
        payload
      });
      const reconciled = await onChanged();
      if (!reconciled) {
        setActionError("The relationship was saved, but the current work context could not be reloaded. Refresh before making another change.");
        return;
      }
      setActionNotice(result.created ? "Relationship added." : "That exact relationship already existed.");
      setCounterpart(null);
      setQuery("");
      setSearchedQuery("");
      setCheckpointId("");
    } catch (error) {
      const conflict = relationshipConflictMessage(error instanceof ApiError ? error.code : undefined);
      setActionError(conflict ?? errorMessage(error));
    } finally {
      setSaving(false);
    }
  }

  async function removeRelationship(relationship: AdjacentRelationshipRead) {
    if (removingId || saving) return;
    const id = relationship.relationship.id;
    setRemovingId(id);
    setActionError("");
    setActionNotice("");
    try {
      const result = await mutationRegistry.execute({
        kind: "remove_relationship",
        slot: `remove-relationship:${work.project_id}:${id}`,
        projectId: work.project_id,
        conflictKeys: [
          mutationWorkKey(work.project_id, work.id),
          mutationWorkKey(work.project_id, relationship.counterpart.id)
        ],
        method: "DELETE",
        path: `/projects/${encodeURIComponent(work.project_id)}/relationships/${encodeURIComponent(id)}`,
        payload: { actor: dashboardMutationActor(dashboardSessionId()) }
      });
      const reconciled = await onChanged();
      if (!reconciled) {
        setActionError("The relationship was removed, but the current work context could not be reloaded. Refresh to reconcile this view.");
        return;
      }
      setActionNotice(result.removed ? "Relationship removed." : "The relationship was already absent.");
    } catch (error) {
      setActionError(errorMessage(error));
    } finally {
      setRemovingId("");
    }
  }

  return <section className="relationship-panel" aria-labelledby="relationships-title">
    <div className="relationship-heading">
      <div>
        <span className="section-label">WORK GRAPH</span>
        <h4 id="relationships-title">Relationships</h4>
      </div>
      <span>{context.relationship_counts.total} edge{context.relationship_counts.total === 1 ? "" : "s"}</span>
    </div>

    {!relationships.length ? <p className="no-relationships">No relationships yet.</p> :
      <div className="relationship-groups">{groupOrder.map((label) => {
        const items = grouped.get(label);
        if (!items?.length) return null;
        return <section className="relationship-group" key={label} aria-labelledby={`relationship-${label.replaceAll(" ", "-").toLowerCase()}`}>
          <h5 id={`relationship-${label.replaceAll(" ", "-").toLowerCase()}`}>{label}</h5>
          <div>{items.map((relationship) => <article className="relationship-row" key={relationship.relationship.id}>
            <div className="relationship-counterpart">
              <div><strong>{relationship.counterpart.title}</strong><StatusBadge status={relationship.counterpart.status} readiness={relationship.counterpart.readiness} /><OperationalBadge readiness={relationship.counterpart.readiness} /></div>
              <span>Added {formatDate(relationship.relationship.created_at)} by {relationship.relationship.created_by_client}</span>
              {relationship.relationship.context_checkpoint_id && <span className="mono" title={relationship.relationship.context_checkpoint_id}>Context checkpoint {relationship.relationship.context_checkpoint_id}</span>}
            </div>
            <button type="button" className="button button-secondary relationship-remove" aria-label={`Remove ${relationshipGroup(relationship)} relationship with ${relationship.counterpart.title}`} disabled={saving || Boolean(removingId) || mutationBlocked} onClick={() => void removeRelationship(relationship)}>{removingId === relationship.relationship.id ? "Removing…" : "Remove"}</button>
          </article>)}</div>
        </section>;
      })}</div>}
    {omittedRelationshipCount > 0 && <p className="no-relationships" role="note">
      Showing {relationships.length} bounded edges; {omittedRelationshipCount} more are available through paged relationship lookup.
    </p>}

    <details className="relationship-editor" open={editorOpen} onToggle={(event) => {
      if (!event.currentTarget.open && mutationBlocked) {
        event.currentTarget.open = true;
        return;
      }
      setEditorOpen(event.currentTarget.open);
    }}>
      <summary>Add a relationship</summary>
      <div className="relationship-editor-body">
        <p className="relationship-semantics">Only an unresolved <strong>blocks</strong> edge changes readiness. Parent/child affects navigation; discovery, duplication, and related edges are descriptive.</p>
        <label className="field">Find another work item<input type="search" value={query} disabled={mutationBlocked} maxLength={500} placeholder="Search titles, summaries, or checkpoints…" onChange={(event) => { setQuery(event.target.value); setCounterpart(null); }} /></label>
        {searching && <div className="relationship-search-status" role="status">Searching…</div>}
        {searchError && <div className="error-notice" role="alert"><p>{searchError}</p></div>}
        {!searching && searchedQuery && !searchPage?.items.length && !searchError && <p className="relationship-search-status">No other work items match.</p>}
        {searchPage?.items.length ? <div className="counterpart-results" role="listbox" aria-label="Matching work items">{searchPage.items.map((result) => <button type="button" role="option" disabled={mutationBlocked} aria-selected={counterpart?.work_item.id === result.work_item.id} className={counterpart?.work_item.id === result.work_item.id ? "selected" : ""} key={result.work_item.id} onClick={() => setCounterpart(result)}><span><strong>{result.work_item.title}</strong><span>{result.work_item.summary}</span></span><StatusBadge status={result.work_item.status} readiness={result.readiness} /></button>)}</div> : null}

        {counterpart && <div className="relationship-fields">
          <label className="field">Relationship type<select value={type} disabled={mutationBlocked} onChange={(event) => setType(event.target.value as RelationshipType)}>{(Object.keys(relationshipTypeLabels) as RelationshipType[]).map((value) => <option value={value} key={value}>{relationshipTypeLabels[value]}</option>)}</select></label>
          {type !== "related" && <fieldset className="relationship-direction"><legend>Direction</legend><label><input type="radio" name="relationship-direction" value="outgoing" checked={direction === "outgoing"} disabled={mutationBlocked} onChange={() => setDirection("outgoing")} /> From this work item</label><label><input type="radio" name="relationship-direction" value="incoming" checked={direction === "incoming"} disabled={mutationBlocked} onChange={() => setDirection("incoming")} /> Toward this work item</label></fieldset>}
          <div className="relationship-preview" role="status"><span>Preview</span><strong>{relationshipPreview(type, direction, work.title, counterpart.work_item.title)}</strong></div>
          <label className="field">{type === "discovered-from" ? "Originating checkpoint" : "Endpoint context checkpoint (optional)"}<select value={checkpointId} required={type === "discovered-from"} disabled={mutationBlocked || checkpointsLoading || Boolean(checkpointError)} onChange={(event) => setCheckpointId(event.target.value)}><option value="">{checkpointsLoading ? "Loading checkpoints…" : type === "discovered-from" ? "Choose a checkpoint…" : "No checkpoint context"}</option>{checkpointChoices.map(({ owner, checkpoint }) => <option key={checkpoint.id} value={checkpoint.id}>{owner} · {checkpoint.kind} · {formatDate(checkpoint.created_at)}</option>)}</select><span className="field-hint">{type === "discovered-from" ? "The checkpoint must belong to the originating target work item." : "Optional context must belong to either endpoint. The newest 100 checkpoints per endpoint are shown."}</span></label>
          {checkpointError && <div className="error-notice" role="alert"><p>{checkpointError}</p><button type="button" className="button button-secondary" onClick={() => setCheckpointRetry((value) => value + 1)}>Try again</button></div>}
          <button type="button" className="button button-primary add-relationship-button" disabled={mutationBlocked || saving || Boolean(removingId) || checkpointsLoading || (type === "discovered-from" && !checkpointId)} onClick={() => void addRelationship()}>{saving ? "Adding…" : "Add relationship"}</button>
        </div>}
      </div>
    </details>
    {actionError && <div className="error-notice relationship-action-message" role="alert"><p>{actionError}</p></div>}
    {actionNotice && <div className="relationship-notice" role="status">{actionNotice}</div>}
  </section>;
}
