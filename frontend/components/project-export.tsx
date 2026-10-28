"use client";

import { useRef, useState } from "react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { errorMessage } from "@/lib/api";
import { artifactStatusOptions } from "@/lib/artifact-queries";
import { backupSize } from "@/lib/backups";
import { knowledgeNextOffset } from "@/lib/knowledge";
import { knowledgeKeys, readDomainPage } from "@/lib/knowledge-queries";
import { downloadProjectExport, readExportArtifacts } from "@/lib/project-export";
import { secondaryKeys } from "@/lib/secondary-queries";

export default function ProjectExportPanel({ projectId, disabled }: { projectId: string; disabled: boolean }) {
  const [domainIds, setDomainIds] = useState<string[]>([]);
  const [artifactIds, setArtifactIds] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const inFlight = useRef(false);
  const domains = useInfiniteQuery({ queryKey: knowledgeKeys.domains(projectId), initialPageParam: 0,
    queryFn: ({ pageParam, signal }) => readDomainPage(projectId, pageParam, signal),
    getNextPageParam: (page) => knowledgeNextOffset(page), retry: false });
  const status = useQuery(artifactStatusOptions());
  const artifacts = useInfiniteQuery({ queryKey: [...secondaryKeys.backups(projectId), "export-artifacts"], initialPageParam: 0,
    queryFn: ({ pageParam, signal }) => readExportArtifacts(projectId, pageParam, signal),
    getNextPageParam: (page) => page.items.length && page.offset + page.items.length < page.total ? page.offset + page.items.length : undefined,
    enabled: status.data?.enabled === true, retry: false });
  const domainItems = domains.data?.pages.flatMap((page) => page.items) ?? [];
  const artifactItems = artifacts.data?.pages.flatMap((page) => page.items) ?? [];
  function toggle(id: string, values: string[], set: (value: string[]) => void) { set(values.includes(id) ? values.filter((value) => value !== id) : [...values, id]); setNotice(""); }
  async function download() {
    if (inFlight.current) return;
    inFlight.current = true; setBusy(true); setError(""); setNotice("");
    try { if (await downloadProjectExport(projectId, domainIds, artifactIds)) setNotice("Export downloaded."); }
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
        {(status.isPending || status.data?.enabled && artifacts.isPending) && <p className="field-hint" role="status">Loading artifacts…</p>}
        {(status.isError || artifacts.isError) && <p className="error-notice" role="alert">{errorMessage(status.error ?? artifacts.error)} <button className="button button-secondary" onClick={() => void (status.isError ? status.refetch() : artifacts.refetch())}>Retry artifacts</button></p>}
        {status.data?.enabled === false && <p className="field-hint">The artifact library is disabled. Enable it to export project files.</p>}
        {artifacts.data && !artifactItems.length && <p className="field-hint">No project artifacts available.</p>}
        <div className="export-choice-list">{artifactItems.map((artifact) => <label className="export-choice" key={artifact.id}><input type="checkbox" checked={artifactIds.includes(artifact.id)} disabled={!artifact.content_available} onChange={() => toggle(artifact.id, artifactIds, setArtifactIds)} /><span><strong>{artifact.filename}</strong><small>{backupSize(artifact.size_bytes)}{artifact.sensitive ? " · Sensitive" : ""}{!artifact.content_available ? " · File unavailable" : ""}</small></span></label>)}</div>
        {artifacts.hasNextPage && <button className="button button-secondary" disabled={artifacts.isFetchingNextPage} onClick={() => void artifacts.fetchNextPage()}>More artifacts</button>}
      </fieldset>
    </div>
    <div className="settings-actions backup-actions"><button type="button" className="button button-primary" disabled={disabled || busy || !domainIds.length && !artifactIds.length} onClick={() => void download()}>{busy ? "Preparing export…" : "Download export"}</button><span className="field-hint">{domainIds.length} domains and {artifactIds.length} artifacts selected</span></div>
    {notice && <p className="backup-notice" role="status">{notice}</p>}
    {error && <p className="error-notice" role="alert">{error}</p>}
  </section>;
}
