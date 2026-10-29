"use client";

import { useRef, useState } from "react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { errorMessage } from "@/lib/api";
import { artifactStatusOptions } from "@/lib/artifact-queries";
import { knowledgeNextOffset } from "@/lib/knowledge";
import { knowledgeKeys, readDomainPage } from "@/lib/knowledge-queries";
import { downloadProjectExport } from "@/lib/project-export";

export default function ProjectExportPanel({ projectId, disabled }: { projectId: string; disabled: boolean }) {
  const [domainIds, setDomainIds] = useState<string[]>([]);
  const [includeArtifacts, setIncludeArtifacts] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const inFlight = useRef(false);
  const domains = useInfiniteQuery({ queryKey: knowledgeKeys.domains(projectId), initialPageParam: 0,
    queryFn: ({ pageParam, signal }) => readDomainPage(projectId, pageParam, signal),
    getNextPageParam: (page) => knowledgeNextOffset(page), retry: false });
  const status = useQuery(artifactStatusOptions());
  const artifactsSelected = includeArtifacts && status.data?.enabled === true;
  const domainItems = domains.data?.pages.flatMap((page) => page.items) ?? [];
  function toggle(id: string, values: string[], set: (value: string[]) => void) { set(values.includes(id) ? values.filter((value) => value !== id) : [...values, id]); setNotice(""); }
  async function download() {
    if (inFlight.current) return;
    inFlight.current = true; setBusy(true); setError(""); setNotice("");
    try { if (await downloadProjectExport(projectId, domainIds, artifactsSelected)) setNotice("Export downloaded."); }
    catch (error) { setError(errorMessage(error)); }
    finally { inFlight.current = false; setBusy(false); }
  }
  return <section className="settings-card project-export" aria-labelledby="project-export-title">
    <div className="settings-card-heading"><div><span className="section-label">PORTABLE FILES</span><h2 id="project-export-title">Export</h2></div></div>
    <p className="settings-intro">Download selected knowledge domains and project artifacts in a .tar.bz2 archive. Domains include their current decision documents; artifacts include their original files. Exported files are for use outside Mnemonic and cannot restore a project.</p>
    <div className="export-selectors">
      <fieldset disabled={disabled || busy}><legend>Knowledge domains</legend>
        {domains.isPending && <p className="field-hint" role="status">Loading domains…</p>}
        {domains.isError && <p className="error-notice" role="alert">{errorMessage(domains.error)} <button className="button button-secondary" onClick={() => void domains.refetch()}>Retry domains</button></p>}
        {!domains.isPending && !domains.isError && !domainItems.length && <p className="field-hint">No knowledge domains available.</p>}
        <div className="export-choice-list">{domainItems.map((domain) => <label className="export-choice" key={domain.id}><input type="checkbox" checked={domainIds.includes(domain.id)} onChange={() => toggle(domain.id, domainIds, setDomainIds)} /><span><strong>{domain.name}</strong><small>{domain.prefix} · {domain.decision_count} documents</small></span></label>)}</div>
        {domains.hasNextPage && <button className="button button-secondary" disabled={domains.isFetchingNextPage} onClick={() => void domains.fetchNextPage()}>More domains</button>}
      </fieldset>
      <fieldset disabled={disabled || busy}><legend>Project artifacts</legend>
        {status.isPending && <p className="field-hint" role="status">Loading artifact availability…</p>}
        {status.isError && <p className="error-notice" role="alert">{errorMessage(status.error)} <button className="button button-secondary" onClick={() => void status.refetch()}>Retry artifacts</button></p>}
        {status.data?.enabled === false && <p className="field-hint">The artifact library is disabled. Enable it to export project files.</p>}
        <label className="export-choice"><input type="checkbox" checked={artifactsSelected} disabled={status.data?.enabled !== true} onChange={(event) => { setIncludeArtifacts(event.target.checked); setNotice(""); }} /><span><strong>Include all project artifacts</strong><small>Includes original files and metadata, including sensitive artifacts.</small></span></label>
      </fieldset>
    </div>
    <div className="settings-actions backup-actions"><button type="button" className="button button-primary" disabled={disabled || busy || !domainIds.length && !artifactsSelected} onClick={() => void download()}>{busy ? "Preparing export…" : "Download export"}</button><span className="field-hint">{domainIds.length} domains selected{artifactsSelected ? " · All project artifacts included" : ""}</span></div>
    {notice && <p className="backup-notice" role="status">{notice}</p>}
    {error && <p className="error-notice" role="alert">{error}</p>}
  </section>;
}
