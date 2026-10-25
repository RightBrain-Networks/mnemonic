"use client";
import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { ApiError, errorMessage } from "@/lib/api";
import { decodeDomain, knowledgeJson, knowledgePath, normalizeDomainPrefix, type KnowledgeDomain } from "@/lib/knowledge";
type DomainIntent = { path: string; method: "POST" | "PATCH"; body: string };

export default function DomainDialog({ projectId, domain, onClose, onSaved, onPendingChange }: {
  projectId: string; domain?: KnowledgeDomain; onClose: () => void; onSaved: (domain: KnowledgeDomain) => void; onPendingChange: (pending: boolean) => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const [name, setName] = useState(domain?.name ?? "");
  const [prefix, setPrefix] = useState(domain?.prefix ?? "");
  const [pending, setPending] = useState<DomainIntent | null>(null);
  const [busy, setBusy] = useState(false);
  const dispatching = useRef(false);
  const [error, setError] = useState("");
  useEffect(() => {
    const dialog = ref.current!;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    dialog.showModal();
    return () => { dialog.close(); previous?.focus({ preventScroll: true }); };
  }, []);

  async function execute(intent: DomainIntent) {
    if (dispatching.current) return;
    dispatching.current = true; setBusy(true); setError(""); setPending(intent); onPendingChange(true);
    try {
      const saved = decodeDomain(await knowledgeJson(intent.path, { method: intent.method, body: intent.body }), projectId);
      if (domain && saved.id !== domain.id) throw new Error("Mnemonic returned a different domain. Retry this exact action.");
      setPending(null); onPendingChange(false); onSaved(saved);
    } catch (failure) {
      const definitive = failure instanceof ApiError && failure.status >= 400 && failure.status < 500 && failure.status !== 408;
      if (definitive) { setPending(null); onPendingChange(false); }
      setError(errorMessage(failure));
    } finally { dispatching.current = false; setBusy(false); }
  }
  function save(event: FormEvent) {
    event.preventDefault();
    try {
      const body = domain ? { name: name.trim(), expected_version: domain.version, client_operation_id: crypto.randomUUID() }
        : { name: name.trim(), prefix: normalizeDomainPrefix(prefix), client_operation_id: crypto.randomUUID() };
      if (!body.name) throw new Error("Enter a domain name.");
      void execute({ path: knowledgePath(projectId, `/domains${domain ? `/${domain.id}` : ""}`), method: domain ? "PATCH" : "POST", body: JSON.stringify(body) });
    } catch (failure) { setError(errorMessage(failure)); }
  }

  return <dialog ref={ref} className="dialog" aria-labelledby={titleId} onCancel={(event) => { event.preventDefault(); if (!pending) onClose(); }}>
    <div className="dialog-header"><h2 id={titleId}>{domain ? "Rename domain" : "Add domain"}</h2><button className="icon-button" aria-label="Close dialog" disabled={Boolean(pending)} onClick={onClose}>×</button></div>
    <div className="dialog-content"><form className="form-stack" onSubmit={save}>
      <label className="field">Domain name<input required autoFocus maxLength={120} value={name} disabled={Boolean(pending)} onChange={(event) => setName(event.target.value)} placeholder="Architecture decisions" /></label>
      <label className="field">Decision prefix<input required maxLength={5} pattern="[A-Za-z]{1,5}" value={prefix} disabled={Boolean(domain) || Boolean(pending)} onChange={(event) => setPrefix(event.target.value.toUpperCase())} placeholder="ADR" /><span className="field-hint">One to five letters (A–Z). The prefix is permanent and forms identifiers such as {prefix || "ADR"}-00001-0.</span></label>
      {error && <div className="error-notice" role="alert"><p>{error}</p>{pending && <><p>The outcome is unconfirmed. Keep this page open and retry the saved action.</p><button type="button" className="button button-secondary" disabled={busy} onClick={() => void execute(pending)}>Retry pending action</button></>}</div>}
      <div className="dialog-actions"><button type="button" className="button button-secondary" disabled={Boolean(pending)} onClick={onClose}>Cancel</button><button type="submit" className="button button-primary" disabled={Boolean(pending)}>{busy ? "Saving…" : domain ? "Save name" : "Create domain"}</button></div>
    </form></div>
  </dialog>;
}

