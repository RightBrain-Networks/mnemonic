"use client";
import { useEffect, useRef, useState, type FormEvent } from "react";
import Link from "@/components/dashboard-link";
import { ApiError, errorMessage } from "@/lib/api";
import { parseDecisionReviewPriority, type DomainDecisionReviewSetting } from "@/lib/automatic-decision-reviews";
import { decodeDomain, knowledgeJson, knowledgePath, type KnowledgeDomain } from "@/lib/knowledge";
type Intent = { path: string; body: string; setting: DomainDecisionReviewSetting; priority: number; version: number };

export default function DomainDecisionReviewSettings({ domain, unavailable, refreshing, blocked, onPendingChange, onReload, onSaved }: {
  domain: KnowledgeDomain;
  unavailable: boolean;
  refreshing: boolean;
  blocked: boolean;
  onPendingChange: (pending: boolean) => void;
  onReload: () => void;
  onSaved: (domain: KnowledgeDomain) => void;
}) {
  const [basis, setBasis] = useState(domain);
  const [draft, setDraft] = useState(domain.automatic_decision_reviews);
  const [priority, setPriority] = useState(String(domain.decision_review_priority));
  const [pending, setPending] = useState<Intent | null>(null);
  const [busy, setBusy] = useState(false);
  const dispatching = useRef(false);
  const [conflict, setConflict] = useState(false);
  const [error, setError] = useState("");
  const parsedPriority = parseDecisionReviewPriority(priority);
  const dirty = draft !== basis.automatic_decision_reviews || parsedPriority !== basis.decision_review_priority;

  useEffect(() => {
    if (pending || domain.version <= basis.version) return;
    if (!dirty) { setBasis(domain); setDraft(domain.automatic_decision_reviews); setPriority(String(domain.decision_review_priority)); }
    else setConflict(true);
  }, [domain, basis.version, dirty, pending]);

  async function execute(intent: Intent) {
    if (dispatching.current) return;
    dispatching.current = true;
    setBusy(true); setError(""); setPending(intent); onPendingChange(true);
    try {
      const saved = decodeDomain(await knowledgeJson(intent.path, { method: "PATCH", body: intent.body }), domain.project_id);
      if (saved.id !== domain.id) throw new Error("Mnemonic returned a different domain. Retry this exact action.");
      if (saved.automatic_decision_reviews !== intent.setting || saved.decision_review_priority !== intent.priority
        || saved.version < intent.version || saved.version > intent.version + 1) {
        throw new Error("Mnemonic returned an inconsistent domain setting. Retry this exact action.");
      }
      setBasis(saved); setDraft(saved.automatic_decision_reviews); setPriority(String(saved.decision_review_priority)); setConflict(false);
      setPending(null); onPendingChange(false); onSaved(saved);
    } catch (failure) {
      const definitive = failure instanceof ApiError && failure.status >= 400 && failure.status < 500 && failure.status !== 408;
      if (definitive) {
        setPending(null); onPendingChange(false);
        if (failure.code === "knowledge_domain_version_conflict") { setConflict(true); onReload(); }
      }
      setError(errorMessage(failure));
    } finally { dispatching.current = false; setBusy(false); }
  }

  function save(event: FormEvent) {
    event.preventDefault();
    if (parsedPriority === null || !dirty || pending || conflict || blocked || unavailable) return;
    void execute({ path: knowledgePath(domain.project_id, `/domains/${domain.id}`), setting: draft, priority: parsedPriority, version: basis.version, body: JSON.stringify({
      expected_version: basis.version, automatic_decision_reviews: draft, decision_review_priority: parsedPriority, client_operation_id: crypto.randomUUID()
    }) });
  }

  const id = `domain-decision-reviews-${domain.id}`;
  return <form className="domain-decision-review-row" onSubmit={save} aria-label={`${domain.prefix} automatic decision impact reviews`}>
    <h3>Automatic decision impact reviews</h3>
    <p className="field-hint">Create a review work item when a document in this domain changes. New domains start off.</p>
    <div className="domain-decision-review-controls">
      <label className="review-policy-toggle" htmlFor={id}>
        <input id={id} type="checkbox" role="switch" checked={draft === "on"} disabled={Boolean(pending) || blocked || unavailable}
          onChange={(event) => { setDraft(event.target.checked ? "on" : "off"); setError(""); }} />
        <strong>Enable automatic reviews for {domain.prefix}</strong>
      </label>
      <label className="field decision-review-priority">Review priority
        <input type="number" min={0} max={100} step={1} required value={priority}
          disabled={Boolean(pending) || blocked || unavailable} aria-invalid={parsedPriority === null || undefined}
          onChange={(event) => { setPriority(event.target.value); setError(""); }} />
        <span className="field-hint">0–100; higher values are more urgent.</span>
      </label>
      <button type="submit" className="button button-secondary"
        disabled={!dirty || parsedPriority === null || Boolean(pending) || conflict || blocked || unavailable}>{busy ? "Saving…" : `Save ${domain.prefix} settings`}</button>
    </div>
    <p className="field-hint">Existing reviews stay available. Edit the investigation instructions in <Link href="/settings/prompts">Prompts</Link>.</p>
    {conflict && <div className="error-notice" role="alert">
      <p>This domain changed. Your draft has been kept. Saved setting: {domain.automatic_decision_reviews}; priority {domain.decision_review_priority}.</p>
      <button type="button" className="button button-secondary" disabled={refreshing || unavailable || Boolean(pending)}
        onClick={() => { setBasis(domain); setConflict(false); setError(""); }}>I reviewed the saved {domain.prefix} setting</button>
    </div>}
    {error && <div className="error-notice" role="alert"><p>{error}</p>{pending && <>
      <p>The outcome is unconfirmed. Keep this page open and retry the saved action.</p>
      <button type="button" className="button button-secondary" disabled={busy} onClick={() => void execute(pending)}>Retry pending {domain.prefix} setting</button>
    </>}</div>}
  </form>;
}
