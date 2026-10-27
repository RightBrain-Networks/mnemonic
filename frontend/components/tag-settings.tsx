"use client";

import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api, ApiError, errorMessage } from "@/lib/api";
import { assertTagMutationResponse, decodeTagCatalog, normalizeTagName, randomTagColor, type ProjectTag } from "@/lib/tags";
import { TagBadge, tagCatalogKey, useProjectTags } from "@/components/work-tags";

type Draft = { id?: string; name: string; description: string; color: string };
type Intent = { path: string; method: string; body: string; message: string };
const emptyDraft = (): Draft => ({ name: "", description: "", color: randomTagColor() });


function TagEditForm({ draft, onChange, blocked, busy, onSave, onCancel, feedback }: {
  draft: Draft; onChange: (draft: Draft) => void; blocked: boolean; busy: boolean;
  onSave: (event: FormEvent) => void; onCancel: () => void; feedback?: ReactNode;
}) {
  return <form className={`tag-edit-form ${draft.id ? "tag-edit-inline" : ""}`} aria-label={draft.id ? "Edit tag" : "New tag"} onSubmit={onSave}>
    <div className="tag-edit-fields">
      <label className="field">Name<input autoFocus required maxLength={50} disabled={blocked} value={draft.name} onChange={(event) => onChange({ ...draft, name: event.target.value })} title={`Saved as ${normalizeTagName(draft.name) || "lower-case-with-dashes"}`} /></label>
      <label className="field tag-edit-description">Description<input required maxLength={240} disabled={blocked} value={draft.description} placeholder="When should this tag be used?" onChange={(event) => onChange({ ...draft, description: event.target.value })} /></label>
      <label className="field">Color<span className="tag-color-fields"><input type="color" aria-label="Tag color" disabled={blocked} value={draft.color} onChange={(event) => onChange({ ...draft, color: event.target.value })} /><input aria-label="Tag color hex" pattern="#[0-9a-fA-F]{6}" maxLength={7} required disabled={blocked} value={draft.color} onChange={(event) => onChange({ ...draft, color: event.target.value })} /><button type="button" className="icon-button" aria-label="Random color" title="Random color" disabled={blocked} onClick={() => onChange({ ...draft, color: randomTagColor() })}>↻</button></span></label>
    </div>
    <div className="tag-edit-footer"><span className="field-hint">Saved as {normalizeTagName(draft.name) || "lower-case-with-dashes"}.</span><div className="settings-actions"><button type="submit" className="button button-primary" disabled={blocked || !normalizeTagName(draft.name)}>{busy ? "Saving…" : "Save tag"}</button><button type="button" className="button button-secondary" disabled={blocked} onClick={onCancel}>Cancel</button></div></div>
    {feedback}
  </form>;
}

export default function TagSettings({ projectId, onNotice, onPendingChange }: { projectId: string; onNotice: (message: string, error?: boolean) => void; onPendingChange: (pending: boolean) => void }) {
  const query = useProjectTags(projectId);
  const client = useQueryClient();
  const [draft, setDraft] = useState<Draft | null>(null);
  const [pending, setPending] = useState<Intent | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [dragged, setDragged] = useState<string | null>(null);
  const intentRef = useRef<Intent | null>(null);
  const tags = query.data?.items ?? [];
  const blocked = busy || pending !== null;
  useEffect(() => { onPendingChange(blocked); }, [blocked, onPendingChange]);
  useEffect(() => () => onPendingChange(false), [onPendingChange]);
  useEffect(() => {
    if (!blocked) return;
    const beforeUnload = (event: BeforeUnloadEvent) => { event.preventDefault(); };
    window.addEventListener("beforeunload", beforeUnload);
    return () => window.removeEventListener("beforeunload", beforeUnload);
  }, [blocked]);

  async function dispatch(intent: Intent) {
    if (busy) return;
    intentRef.current = intent;
    setPending(intent); setBusy(true); setError("");
    try {
      const saved = decodeTagCatalog(await api(intent.path, { method: intent.method, body: intent.body }), projectId);
      assertTagMutationResponse(saved, intent.method, intent.path, JSON.parse(intent.body));
      client.setQueryData(tagCatalogKey(projectId), saved);
      void client.invalidateQueries({ predicate: (query) => query.queryKey.includes(projectId) });
      intentRef.current = null; setPending(null); setDraft(null);
      onNotice(intent.message);
    } catch (reason) {
      const uncertain = !(reason instanceof ApiError) || reason.status === 0 || reason.status >= 500;
      setError(uncertain ? `${errorMessage(reason)} The save outcome is uncertain. Retry the same action to recover its result.` : errorMessage(reason));
      if (!uncertain) { intentRef.current = null; setPending(null); }
    } finally { setBusy(false); }
  }
  function mutate(method: string, suffix: string, fields: Record<string, unknown>, message: string) {
    if (intentRef.current || blocked) return;
    const intent = { path: `/projects/${projectId}/tags${suffix}`, method, body: JSON.stringify({ ...fields, client_operation_id: crypto.randomUUID() }), message };
    void dispatch(intent);
  }
  function save(event: FormEvent) {
    event.preventDefault();
    if (!draft) return;
    mutate(draft.id ? "PATCH" : "POST", draft.id ? `/${draft.id}` : "", { name: normalizeTagName(draft.name), description: draft.description.trim(), color: draft.color }, draft.id ? "Tag saved." : "Tag created.");
  }
  function reorder(id: string, targetId: string) {
    if (blocked || id === targetId) return;
    const order = tags.map((tag) => tag.id), from = order.indexOf(id), to = order.indexOf(targetId);
    if (from < 0 || to < 0) return;
    order.splice(from, 1); order.splice(to, 0, id);
    mutate("PUT", "/order", { tag_ids: order }, "Tag order saved.");
  }
  function remove(tag: ProjectTag) {
    if (!window.confirm(`Delete “${tag.name}” and remove it from work items and artifacts?`)) return;
    mutate("DELETE", `/${tag.id}`, {}, "Tag deleted.");
  }

  const feedback = (error || query.error) && <div className="error-notice" role="alert"><p>{error || errorMessage(query.error)}</p>{pending ? <button type="button" className="button button-secondary" disabled={busy} onClick={() => void dispatch(pending)}>{busy ? "Saving…" : "Retry same action"}</button> : <button type="button" className="text-link" onClick={() => void query.refetch()}>Reload tags</button>}</div>;
  const cancelEdit = () => { setDraft(null); setError(""); };

  return <section className="settings-card tag-settings" aria-labelledby="tag-settings-title">
    <div className="settings-card-heading"><div><span className="section-label">PROJECT ORGANIZATION</span><h2 id="tag-settings-title">Tags</h2></div><button type="button" className="button button-primary" disabled={blocked || draft !== null} onClick={() => setDraft(emptyDraft())}>New tag</button></div>
    <p className="settings-intro">Describe when each tag should be used. Drag tags to change their order in the dashboard filter. System tags are managed by Mnemonic.</p>
    {query.isPending && <p role="status">Loading tags…</p>}
    {!draft?.id && feedback}
    {draft && !draft.id && <TagEditForm draft={draft} onChange={setDraft} blocked={blocked} busy={busy} onSave={save} onCancel={cancelEdit} />}
    <ol className="tag-settings-list" aria-label="Project tags">{tags.map((tag, index) => <li key={tag.id} className={dragged === tag.id ? "is-dragging" : ""} draggable={!blocked && draft === null} onDragStart={(event) => { event.dataTransfer.setData("text/plain", tag.id); setDragged(tag.id); }} onDragEnd={() => setDragged(null)} onDragOver={(event) => { if (dragged) event.preventDefault(); }} onDrop={(event) => { event.preventDefault(); if (dragged) reorder(dragged, tag.id); setDragged(null); }}>
      {draft?.id === tag.id ? <TagEditForm draft={draft} onChange={setDraft} blocked={blocked} busy={busy} onSave={save} onCancel={cancelEdit} feedback={feedback} /> : <>
      <span className="tag-drag-handle" aria-hidden="true">⠿</span><div className="tag-settings-content"><TagBadge name={tag.name} tag={tag} /><p>{tag.description}</p></div>
      {tag.system && <span className="tag-system-note">System</span>}
      <div className="tag-row-actions"><button type="button" className="icon-button" aria-label={`Move ${tag.name} up`} disabled={blocked || draft !== null || index === 0} onClick={() => reorder(tag.id, tags[index - 1].id)}>↑</button><button type="button" className="icon-button" aria-label={`Move ${tag.name} down`} disabled={blocked || draft !== null || index === tags.length - 1} onClick={() => reorder(tag.id, tags[index + 1].id)}>↓</button>{!tag.system && <><button type="button" className="button button-secondary" disabled={blocked || draft !== null} onClick={() => { setDraft({ id: tag.id, name: tag.name, description: tag.description, color: tag.color }); setError(""); }}>Edit</button><button type="button" className="button button-secondary" disabled={blocked || draft !== null} onClick={() => remove(tag)}>Delete</button></>}</div>
      </>}
    </li>)}</ol>
  </section>;
}
