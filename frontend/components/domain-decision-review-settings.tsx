"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { useInfiniteQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, errorMessage } from "@/lib/api";
import { effectiveDecisionReviews, type DomainDecisionReviewSetting } from "@/lib/automatic-decision-reviews";
import { decodeDomain, knowledgeJson, knowledgeNextOffset, knowledgePath, type KnowledgeDomain } from "@/lib/knowledge";
import { knowledgeKeys, readDomainPage } from "@/lib/knowledge-queries";

type Intent = { path: string; body: string; setting: DomainDecisionReviewSetting; version: number };

export default function DomainDecisionReviewSettings({ projectId, projectEnabled, onNotice, onPendingChange }: {
  projectId: string;
  projectEnabled: boolean;
  onNotice: (message: string, error?: boolean) => void;
  onPendingChange: (pending: boolean) => void;
}) {
  const client = useQueryClient();
  const [pendingDomain, setPendingDomain] = useState<KnowledgeDomain | null>(null);
  const domains = useInfiniteQuery({
    queryKey: knowledgeKeys.domains(projectId), initialPageParam: 0,
    queryFn: ({ pageParam, signal }) => readDomainPage(projectId, pageParam, signal),
    getNextPageParam: (last) => knowledgeNextOffset(last), retry: false,
    refetchInterval: 30_000, refetchIntervalInBackground: false
  });
  const items = domains.data?.pages.flatMap((page) => page.items) ?? [];
  // A refresh can move a domain off the loaded pages. Keep its keyed row mounted
  // until its frozen mutation has a known outcome, including through read failures.
  const rows = pendingDomain && !items.some((domain) => domain.id === pendingDomain.id)
    ? [...items, pendingDomain] : items;

  return <div className="domain-decision-review-settings">
    <h3>Knowledge domains</h3>
    <p className="settings-intro">Choose whether changes to records in each domain start automatic impact reviews.
      Inherit follows the saved project default; On and Off override it. New domains start with Inherit.</p>
    <p className="field-hint">The changed record’s domain governs. Existing reviews and captured assessments remain available.</p>
    {domains.isError && <div className="error-notice" role="alert">
      <p>{errorMessage(domains.error)}</p>
      <button type="button" className="button button-secondary" onClick={() => void domains.refetch()}>Reload domains</button>
    </div>}
    {domains.isPending && <p role="status">Loading knowledge domains…</p>}
    {rows.map((domain) => <DomainReviewRow key={domain.id} domain={domain} projectEnabled={projectEnabled}
      unavailable={domains.isError} refreshing={domains.isFetching}
      blocked={pendingDomain !== null && pendingDomain.id !== domain.id}
      onPendingChange={(pending) => { setPendingDomain(pending ? domain : null); onPendingChange(pending); }}
      onReload={() => void domains.refetch()}
      onSaved={(saved) => {
        onNotice(`Automatic decision impact reviews for ${saved.name} saved.`);
        void client.invalidateQueries({ queryKey: knowledgeKeys.root(projectId) });
      }} />)}
    {domains.hasNextPage && <button type="button" className="button button-secondary"
      disabled={domains.isFetchingNextPage} onClick={() => void domains.fetchNextPage()}>More domains</button>}
    {!domains.isPending && !domains.isError && !rows.length && <p>No knowledge domains are available.</p>}
  </div>;
}

function DomainReviewRow({ domain, projectEnabled, unavailable, refreshing, blocked, onPendingChange, onReload, onSaved }: {
  domain: KnowledgeDomain;
  projectEnabled: boolean;
  unavailable: boolean;
  refreshing: boolean;
  blocked: boolean;
  onPendingChange: (pending: boolean) => void;
  onReload: () => void;
  onSaved: (domain: KnowledgeDomain) => void;
}) {
  const [basis, setBasis] = useState(domain);
  const [draft, setDraft] = useState(domain.automatic_decision_reviews);
  const [pending, setPending] = useState<Intent | null>(null);
  const [busy, setBusy] = useState(false);
  const dispatching = useRef(false);
  const [conflict, setConflict] = useState(false);
  const [error, setError] = useState("");
  const dirty = draft !== basis.automatic_decision_reviews;

  useEffect(() => {
    if (pending || domain.version <= basis.version) return;
    if (!dirty) { setBasis(domain); setDraft(domain.automatic_decision_reviews); }
    else setConflict(true);
  }, [domain, basis.version, dirty, pending]);

  async function execute(intent: Intent) {
    if (dispatching.current) return;
    dispatching.current = true;
    setBusy(true); setError(""); setPending(intent); onPendingChange(true);
    try {
      const saved = decodeDomain(await knowledgeJson(intent.path, { method: "PATCH", body: intent.body }), domain.project_id);
      if (saved.id !== domain.id) throw new Error("Mnemonic returned a different domain. Retry this exact action.");
      if (saved.automatic_decision_reviews !== intent.setting
        || saved.version < intent.version || saved.version > intent.version + 1) {
        throw new Error("Mnemonic returned an inconsistent domain setting. Retry this exact action.");
      }
      setBasis(saved); setDraft(saved.automatic_decision_reviews); setConflict(false);
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
    if (!dirty || pending || conflict || blocked || unavailable) return;
    void execute({ path: knowledgePath(domain.project_id, `/domains/${domain.id}`), setting: draft, version: basis.version, body: JSON.stringify({
      expected_version: basis.version, automatic_decision_reviews: draft, client_operation_id: crypto.randomUUID()
    }) });
  }

  const id = `domain-decision-reviews-${domain.id}`;
  const saved = basis.version > domain.version ? basis : domain;
  return <form className="domain-decision-review-row" onSubmit={save} aria-label={`${domain.prefix} automatic decision impact reviews`}>
    <div className="domain-decision-review-controls">
      <label className="field" htmlFor={id}><strong>{domain.name} <span className="knowledge-prefix">{domain.prefix}</span></strong>
        <select id={id} value={draft} disabled={Boolean(pending) || blocked || unavailable}
          aria-describedby={`${id}-effective`} onChange={(event) => { setDraft(event.target.value as DomainDecisionReviewSetting); setError(""); }}>
          <option value="inherit">Inherit project default</option><option value="on">On</option><option value="off">Off</option>
        </select>
      </label>
      <button type="submit" className="button button-secondary"
        disabled={!dirty || Boolean(pending) || conflict || blocked || unavailable}>{busy ? "Saving…" : `Save ${domain.prefix} setting`}</button>
    </div>
    <p className="field-hint" id={`${id}-effective`}>Saved effective setting: {effectiveDecisionReviews(saved.automatic_decision_reviews, projectEnabled) ? "On" : "Off"}
      {saved.automatic_decision_reviews === "inherit" ? " (inherits project default)." : " (domain override)."}</p>
    {conflict && <div className="error-notice" role="alert">
      <p>This domain changed. Your draft has been kept. Saved setting: {domain.automatic_decision_reviews}.</p>
      <button type="button" className="button button-secondary" disabled={refreshing || unavailable || Boolean(pending)}
        onClick={() => { setBasis(domain); setConflict(false); setError(""); }}>I reviewed the saved {domain.prefix} setting</button>
    </div>}
    {error && <div className="error-notice" role="alert"><p>{error}</p>{pending && <>
      <p>The outcome is unconfirmed. Keep this page open and retry the saved action.</p>
      <button type="button" className="button button-secondary" disabled={busy} onClick={() => void execute(pending)}>Retry pending {domain.prefix} setting</button>
    </>}</div>}
  </form>;
}
