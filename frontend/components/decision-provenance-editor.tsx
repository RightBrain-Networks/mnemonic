"use client";

import { useEffect, type Dispatch, type SetStateAction } from "react";
import { useQuery } from "@tanstack/react-query";
import { emptyProvenanceDraft, type DecisionProvenance } from "@/lib/decision-provenance";
import { errorMessage } from "@/lib/api";
import { completionDomainsOptions } from "@/lib/knowledge-queries";
import type { JobReportDraft } from "@/lib/job-completion-reports";

export default function DecisionProvenanceEditor({ projectId, draft, onChange }: { projectId: string; draft: JobReportDraft; onChange: Dispatch<SetStateAction<JobReportDraft>> }) {
  const domains = useQuery({ ...completionDomainsOptions(projectId), refetchOnMount: "always" });
  const requirement = domains.isError ? "error" : domains.isPending ? "loading" : domains.data.total > 0 ? "required" : "optional";
  useEffect(() => { onChange((current) => current.provenanceRequirement === requirement ? current : { ...current, provenanceRequirement: requirement }); }, [onChange, requirement]);
  const value = draft.decisionProvenance ?? emptyProvenanceDraft();
  const update = (patch: Partial<typeof value>) => onChange((current) => ({ ...current, decisionProvenance: { ...(current.decisionProvenance ?? emptyProvenanceDraft()), ...patch } }));
  if (domains.isError) return <div className="error-notice" role="alert"><p>{errorMessage(domains.error)} Decision domains must be checked before completion.</p><button type="button" className="button button-secondary" onClick={() => void domains.refetch()}>Retry decision domains</button></div>;
  if (domains.isPending) return <p role="status">Checking decision domains…</p>;
  if (requirement === "optional" && !draft.decisionProvenance) return null;
  return <section className="decision-provenance-editor" aria-label="Decision provenance">
    <h4>Decision provenance</h4><p className="field-hint">Declare which decisions informed this work. Citations register the exact decision revisions and repository commits; they do not verify the code or Git history.</p>
    <label className="field">Decision use<select aria-required={requirement === "required"} value={value.disposition} onChange={(event) => { const disposition = event.target.value as DecisionProvenance["disposition"] | ""; update({ disposition, citations: disposition === "cited" ? value.citations : [], commits: disposition === "not-code" ? [] : value.commits }); }}>
      <option value="">Choose how decisions apply</option><option value="cited">Cited decisions</option><option value="no-applicable-decisions">No applicable decisions</option><option value="not-code">Not code</option>
    </select></label>
    {value.disposition && <label className="field">{value.disposition === "cited" ? "Explanation (optional)" : "Reason"}<textarea rows={2} maxLength={2000} aria-required={value.disposition !== "cited"} value={value.reason} onChange={(event) => update({ reason: event.target.value })} /></label>}
    {value.disposition === "cited" && <>
      {value.citations.map((entry, index) => <div className="provenance-citation-row" key={index}>
        <label className="field">Decision ID {index + 1}<input aria-required="true" placeholder="ADR-00001" value={entry.decision_id} onChange={(event) => update({ citations: value.citations.map((item, position) => position === index ? { ...item, decision_id: event.target.value.toUpperCase() } : item) })} /></label>
        <label className="field">Revision<input aria-required="true" type="number" min={0} max={2147483647} step={1} value={entry.revision} onChange={(event) => update({ citations: value.citations.map((item, position) => position === index ? { ...item, revision: event.target.value } : item) })} /></label>
        <button type="button" className="button button-secondary" aria-label={`Remove decision citation ${index + 1}`} onClick={() => update({ citations: value.citations.filter((_, position) => position !== index) })}>Remove</button>
      </div>)}
      <button type="button" className="button button-secondary" disabled={value.citations.length >= 100} onClick={() => update({ citations: [...value.citations, { decision_id: "", revision: "" }] })}>Add decision citation</button>
    </>}
    {(value.disposition === "cited" || value.disposition === "no-applicable-decisions") && <>
      {value.commits.map((entry, index) => <div className="provenance-commit-row" key={index}>
        <label className="field">Repository {index + 1}<input aria-required="true" maxLength={500} value={entry.repository} placeholder="github.com/organization/repository" onChange={(event) => update({ commits: value.commits.map((item, position) => position === index ? { ...item, repository: event.target.value } : item) })} /></label>
        <label className="field">Commit hash {index + 1}<input aria-required="true" maxLength={64} className="mono" value={entry.commit_hash} onChange={(event) => update({ commits: value.commits.map((item, position) => position === index ? { ...item, commit_hash: event.target.value } : item) })} /><span className="field-hint">Full 40- or 64-character hash. Each commit is linked to every citation above.</span></label>
        <button type="button" className="button button-secondary" aria-label={`Remove implementation commit ${index + 1}`} onClick={() => update({ commits: value.commits.filter((_, position) => position !== index) })}>Remove commit</button>
      </div>)}
      <button type="button" className="button button-secondary" disabled={value.commits.length >= 100} onClick={() => update({ commits: [...value.commits, { repository: "", commit_hash: "" }] })}>Add implementation commit</button>
    </>}
  </section>;
}
