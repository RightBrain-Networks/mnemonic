"use client";

import { useEffect, useId, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { errorMessage } from "@/lib/api";
import { decodeTranscriptFolders } from "@/lib/transcript-settings";
import { transcriptPath, transcriptRequest, validTranscriptDirectory } from "@/lib/transcripts";

export default function TranscriptFolderDialog({ projectId, onClose, onSelect }: {
  projectId: string; onClose: () => void; onSelect: (directory: string) => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const [directory, setDirectory] = useState<string | null>(null);
  const [address, setAddress] = useState("");
  const [filter, setFilter] = useState("");
  const [query, setQuery] = useState("");
  useEffect(() => { const timer = window.setTimeout(() => setQuery(filter.trim()), 200); return () => window.clearTimeout(timer); }, [filter]);
  const params = new URLSearchParams();
  if (directory) params.set("directory", directory);
  if (query) params.set("query", query);
  const folders = useQuery({
    queryKey: ["transcript-folders", projectId, directory, query],
    queryFn: async ({ signal }) => decodeTranscriptFolders(await transcriptRequest(
      `${transcriptPath(projectId)}/browse${params.size ? `?${params}` : ""}`, { signal })),
    retry: false
  });
  useEffect(() => {
    const element = dialog.current!;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    element.showModal();
    return () => { element.close(); if (previous?.isConnected) previous.focus({ preventScroll: true }); };
  }, []);
  function navigate(path: string | null) { setDirectory(path); setAddress(path ?? ""); setFilter(""); setQuery(""); }
  return <dialog ref={dialog} className="dialog transcript-folder-dialog" aria-labelledby={titleId} onCancel={(event) => { event.preventDefault(); onClose(); }}>
    <div className="dialog-header"><h2 id={titleId}>Choose a transcript folder</h2><button className="icon-button" aria-label="Close folder browser" onClick={onClose}>×</button></div>
    <div className="dialog-content">
      <p className="field-hint">Browse folders on the Mnemonic server. Select a folder containing sessions for this project.</p>
      <form className="transcript-folder-address" onSubmit={(event) => { event.preventDefault(); if (validTranscriptDirectory(address)) navigate(address); }}>
        <label className="field">Server folder<input autoFocus value={address} placeholder="Browse below or enter a folder path" autoComplete="off" spellCheck={false} onChange={(event) => setAddress(event.target.value)} /></label>
        <button className="button button-secondary" disabled={!validTranscriptDirectory(address)}>Go</button>
      </form>
      <nav className="transcript-folder-nav" aria-label="Folder navigation"><button type="button" className="text-button" disabled={directory === null} onClick={() => navigate(null)}>Shared folders</button>{directory && <><span aria-hidden="true">/</span><button type="button" className="text-button" disabled={folders.isFetching} onClick={() => navigate(folders.data?.parent ?? null)}>Up one level</button></>}</nav>
      <div className="transcript-folder-current"><span className="section-label">{directory ? "Current folder" : "Available folders"}</span>{directory && <code>{directory}</code>}</div>
      <label className="field transcript-folder-filter">Filter folders<input type="search" value={filter} maxLength={200} placeholder="Find a folder by name…" onChange={(event) => setFilter(event.target.value)} /></label>
      <div className="transcript-folder-list" aria-busy={folders.isFetching}>
        {folders.isPending && <p role="status">Loading folders…</p>}
        {folders.error && <div className="error-notice" role="alert"><p>{errorMessage(folders.error)}</p><button className="button button-secondary" onClick={() => void folders.refetch()}>Retry</button></div>}
        {folders.data && <><ul aria-label="Server folders">{folders.data.entries.map((entry) => <li key={entry.path}><button type="button" onClick={() => navigate(entry.path)} aria-label={`Open ${entry.name}`}>
          <svg width="21" height="18" viewBox="0 0 24 20" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="M2 18V2h7l3 3h10v13z" /></svg><span>{entry.name}</span><span aria-hidden="true">›</span>
        </button></li>)}</ul>{!folders.data.entries.length && <p className="transcript-folder-empty">{query ? "No folders match your filter." : directory ? "No subfolders. You can select this folder." : "No shared folders are available. Configure transcript sources on the server first."}</p>}
          {folders.data.truncated && <p role="status">Only some folders are shown. Filter by name or enter a specific path above.</p>}</>}
      </div>
      <div className="dialog-actions"><button className="button button-secondary" onClick={onClose}>Cancel</button><button className="button button-primary" disabled={!folders.data?.directory || folders.isFetching || folders.isError} onClick={() => { if (folders.data?.directory) onSelect(folders.data.directory); }}>Select folder</button></div>
    </div>
  </dialog>;
}
