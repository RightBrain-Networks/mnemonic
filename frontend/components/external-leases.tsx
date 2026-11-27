import { useEffect, useRef, useState, type FormEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, ApiError, errorMessage } from "@/lib/api";
import { formatDateTime } from "@/lib/display-time";
import {
  decodeResource, decodeResources, MAX_RESOURCE_CAPACITY, parseResourceDraft,
  type ExternalResource, type ResourceDefinition, type ResourceDraft, type ResourceLease
} from "@/lib/external-resources";
import { earliestLeaseExpiry, scheduleLeaseExpiryRefresh } from "@/lib/lease-refresh";
import { MAX_LEASE_MINUTES } from "@/lib/work-lease-settings";
import { objectValue, sameUuid } from "@/lib/wire-guards";

type Action = {
  path: string;
  method: "POST" | "PUT";
  body: string;
  resourceId?: string;
  leaseId?: string;
  definition?: ResourceDefinition;
};

type Editor = { id?: string; draft: ResourceDraft };

function editor(resource?: ExternalResource): Editor {
  return { id: resource?.id, draft: { name: resource?.name ?? "", capacity: String(resource?.capacity ?? 1),
    minimum_minutes: String(resource?.minimum_minutes ?? 10), default_minutes: String(resource?.default_minutes ?? 15),
    maximum_minutes: String(resource?.maximum_minutes ?? 120) } };
}

export default function ExternalLeases({ projectId, onPendingChange }: {
  projectId: string; onPendingChange: (pending: boolean) => void;
}) {
  const base = `/projects/${projectId}/external-resources`;
  const query = useQuery({
    queryKey: ["external-resources", projectId],
    queryFn: async ({ signal }) => decodeResources(await api(base, { signal }), projectId),
    refetchInterval: 30_000
  });
  const resources = query.data ?? [];
  const expiry = earliestLeaseExpiry(resources.flatMap((resource) => resource.leases.map((lease) => lease.expires_at)));
  const { refetch } = query;
  useEffect(() => expiry ? scheduleLeaseExpiryRefresh(expiry, () => { void refetch(); }) : undefined,
    [expiry, refetch]);
  const [editing, setEditing] = useState<Editor | null>(null);
  const [pending, setPending] = useState<Action | null>(null);
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    if (!pending) return;
    onPendingChange(true);
    const beforeUnload = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", beforeUnload);
    return () => { onPendingChange(false); window.removeEventListener("beforeunload", beforeUnload); };
  }, [pending, onPendingChange]);

  async function submit(action: Action) {
    if (inFlight.current) return;
    inFlight.current = true;
    setPending(action);
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const response = await api(action.path, { method: action.method, body: action.body });
      if (action.definition) {
        const saved = decodeResource(response, projectId);
        if (action.resourceId && !sameUuid(saved.id, action.resourceId)
          || !Object.entries(action.definition).every(([key, value]) => saved[key as keyof ResourceDefinition] === value)) {
          throw new Error("Mnemonic returned a different resource definition.");
        }
      } else {
        const saved = objectValue(response);
        if (!saved || !sameUuid(saved.project_id, projectId) || !sameUuid(saved.resource_id, action.resourceId)
          || !sameUuid(saved.lease_id, action.leaseId) || saved.released !== true) {
          throw new Error("Mnemonic returned a different lease release.");
        }
      }
      setEditing(null);
      setPending(null);
      setNotice(action.leaseId ? "Lease released." : "Resource saved.");
      await refetch();
    } catch (reason) {
      const uncertain = !(reason instanceof ApiError) || reason.status === 0 || reason.status >= 500;
      setError(uncertain ? `${errorMessage(reason)} Retry this action to confirm its outcome.` : errorMessage(reason));
      if (!uncertain) { setPending(null); void refetch(); }
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }

  const parsed = editing ? parseResourceDraft(editing.draft) : null;
  function save(event: FormEvent) {
    event.preventDefault();
    if (!editing || !parsed || pending) return;
    void submit({ path: editing.id ? `${base}/${editing.id}` : base, method: editing.id ? "PUT" : "POST",
      body: JSON.stringify({ ...parsed, client_operation_id: crypto.randomUUID() }),
      resourceId: editing.id, definition: parsed });
  }
  function release(resource: ExternalResource, lease: ResourceLease) {
    if (pending || !window.confirm(`Release ${lease.quantity} units of “${resource.name}” held by ${lease.holder_client} (${lease.holder_session_id})?`)) return;
    void submit({ path: `${base}/${resource.id}/leases/${lease.id}/revoke`, method: "POST",
      body: JSON.stringify({ client_operation_id: crypto.randomUUID() }), resourceId: resource.id, leaseId: lease.id });
  }
  function startEdit(resource?: ExternalResource) {
    setError(""); setNotice(""); setEditing(editor(resource));
  }

  const disabled = busy || Boolean(pending);
  const leases = resources.flatMap((resource) => resource.leases.map((lease) => ({ resource, lease })));
  return <div className="external-leases">
    {query.error && <div className="error-notice" role="alert"><p>{errorMessage(query.error)}</p>
      <button className="text-link" onClick={() => void refetch()}>Reload resources</button></div>}
    {error && <div className="error-notice" role="alert"><p>{error}</p>
      {pending && <button type="button" className="button button-secondary" disabled={busy}
        onClick={() => void submit(pending)}>Retry same action</button>}</div>}
    {notice && <p className="field-hint" role="status">{notice}</p>}
    <section className="settings-card" aria-labelledby="external-resource-heading">
      <div className="settings-card-heading"><h2 id="external-resource-heading">Resources</h2>
        <button className="button button-primary" type="button" onClick={() => startEdit()}
          disabled={disabled || Boolean(editing)}>New resource</button></div>
      {editing && <form className="external-resource-editor form-stack" aria-label={editing.id ? "Edit resource" : "New resource"} onSubmit={save}>
        <label className="field">Resource name<input required autoFocus maxLength={200} value={editing.draft.name} disabled={disabled}
          onChange={(event) => setEditing({ ...editing, draft: { ...editing.draft, name: event.target.value } })} /></label>
        <div className="work-lease-fields">{([
          ["capacity", "Total units", MAX_RESOURCE_CAPACITY], ["minimum_minutes", "Minimum (minutes)", MAX_LEASE_MINUTES],
          ["default_minutes", "Default (minutes)", MAX_LEASE_MINUTES], ["maximum_minutes", "Maximum (minutes)", MAX_LEASE_MINUTES]
        ] as const).map(([key, label, max]) => <label key={key} className="field">{label}
          <input required type="number" min={1} max={max} step={1} disabled={disabled} value={editing.draft[key]}
            onChange={(event) => setEditing({ ...editing, draft: { ...editing.draft, [key]: event.target.value } })} />
        </label>)}</div>
        {!parsed && editing.draft.name.trim() && <p className="field-hint" role="alert">Enter 1–4,294,967,295 whole units and positive whole minutes. The default duration must be between the minimum and maximum.</p>}
        <div className="settings-actions"><button className="button button-primary" disabled={!parsed || disabled}>Save resource</button>
          <button className="button button-secondary" type="button" disabled={disabled} onClick={() => { setEditing(null); setError(""); }}>Cancel</button></div>
      </form>}
      {query.isPending && <p role="status">Loading resources…</p>}
      {query.data && !resources.length && !editing && <p>No resources defined.</p>}
      {resources.length > 0 && <ul className="external-resource-list">{resources.map((resource) => <li key={resource.id}>
        <div><h3>{resource.name}</h3><p>{resource.available.toLocaleString()} of {resource.capacity.toLocaleString()} units available</p>
          <p className="field-hint">Default {resource.default_minutes} min · Range {resource.minimum_minutes}–{resource.maximum_minutes} min</p></div>
        <button className="button button-secondary" type="button" disabled={disabled || Boolean(editing)}
          aria-label={`Edit ${resource.name}`} onClick={() => startEdit(resource)}>Edit</button>
      </li>)}</ul>}
    </section>
    <section className="settings-card" aria-labelledby="external-active-heading">
      <div className="settings-card-heading"><h2 id="external-active-heading">Active leases</h2></div>
      {query.data && !leases.length && <p>No active leases.</p>}
      {leases.length > 0 && <ul className="external-resource-list">{leases.map(({ resource, lease }) => <li key={lease.id}>
        <div><h3>{resource.name}</h3><p>{lease.quantity.toLocaleString()} {lease.quantity === 1 ? "unit" : "units"} held by {lease.holder_client}</p>
          <p className="field-hint">Session {lease.holder_session_id}</p>
          <p className="field-hint">Expires <time dateTime={lease.expires_at}>{formatDateTime(lease.expires_at)}</time></p></div>
        <button className="text-link" type="button" disabled={disabled || Boolean(editing)}
          aria-label={`Release ${resource.name} lease for ${lease.holder_session_id}`}
          onClick={() => release(resource, lease)}>Release</button>
      </li>)}</ul>}
    </section>
  </div>;
}
