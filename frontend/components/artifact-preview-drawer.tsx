"use client";

import { useEffect, useId, useRef, useState } from "react";
import MarkdownContent from "@/components/markdown-content";
import { artifactPath, type Artifact } from "@/lib/artifacts";
import { type ArtifactPreviewKind } from "@/lib/artifact-preview";
import { readBoundedBytes } from "@/lib/bounded-json";

export default function ArtifactPreviewDrawer({ artifact, kind, onClose }: {
  artifact: Artifact;
  kind: ArtifactPreviewKind;
  onClose: () => void;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const [text, setText] = useState<string | null>(null);
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [copyStatus, setCopyStatus] = useState("");

  useEffect(() => {
    const dialog = dialogRef.current!;
    const returnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const previousOverflow = document.body.style.overflow;
    dialog.showModal();
    document.body.style.overflow = "hidden";
    return () => {
      dialog.close();
      document.body.style.overflow = previousOverflow;
      if (returnFocus?.isConnected) returnFocus.focus({ preventScroll: true });
    };
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    let objectUrl: string | undefined;
    async function load() {
      try {
        const response = await fetch(`${artifactPath(artifact.project_id, artifact.id)}/content`, {
          cache: "no-store", signal: controller.signal
        });
        if (!response.ok) throw new Error();
        const bytes = await readBoundedBytes(response, 1024 * 1024 * 1024);
        if (controller.signal.aborted) return;
        if (kind === "image") {
          objectUrl = URL.createObjectURL(new Blob([bytes], { type: artifact.mime_type!.split(";")[0].trim().toLowerCase() }));
          setImageUrl(objectUrl);
        } else {
          setText(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
        }
      } catch {
        if (!controller.signal.aborted) setError("Unable to preview this file. Close the preview and try again, or download the file.");
      }
    }
    void load();
    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [artifact.project_id, artifact.id, artifact.mime_type, kind]);

  async function copyContents() {
    if (text === null) return;
    try {
      await navigator.clipboard.writeText(text);
      setCopyStatus("Contents copied.");
    } catch { setCopyStatus("Unable to copy. Select the contents and copy them manually."); }
  }

  return <dialog ref={dialogRef} className="artifact-preview-drawer" aria-labelledby={titleId}
    onCancel={(event) => { event.preventDefault(); onClose(); }}
    onDragEnter={(event) => { event.preventDefault(); event.stopPropagation(); }}
    onDragOver={(event) => { event.preventDefault(); event.stopPropagation(); event.dataTransfer.dropEffect = "none"; }}
    onDrop={(event) => { event.preventDefault(); event.stopPropagation(); }}
    onClick={(event) => {
      if (event.target !== event.currentTarget) return;
      const bounds = event.currentTarget.getBoundingClientRect();
      if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) onClose();
    }}>
    <header className="artifact-preview-header">
      <div><span className="section-label">Artifact preview</span><h2 id={titleId} title={artifact.filename}>{artifact.filename}</h2></div>
      <div className="artifact-preview-actions">
        {kind !== "image" && <button type="button" className="icon-button" aria-label="Copy contents" title="Copy contents" disabled={text === null || Boolean(error)} onClick={() => void copyContents()}>
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true"><rect x="8" y="8" width="12" height="13" rx="2" /><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3" /></svg>
        </button>}
        <button type="button" className="icon-button" aria-label="Close preview" title="Close preview (Esc)" autoFocus onClick={onClose}>
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18" /></svg>
        </button>
      </div>
    </header>
    {artifact.sensitive && <p className="artifact-preview-notice"><span className="artifact-sensitive-badge">Sensitive</span> Agents need explicit human approval for each content access.</p>}
    {copyStatus && <p className="artifact-preview-notice" role="status">{copyStatus}</p>}
    <div className="artifact-preview-body">
      {error ? <p className="error-notice" role="alert">{error}</p> : kind === "image" && imageUrl ? <>
        <div className="artifact-preview-image"><img src={imageUrl} alt={artifact.filename} onError={() => setError("This image cannot be displayed by your browser. Download the file to view it.")} /></div>
        <a className="artifact-preview-image-link" href={imageUrl} target="_blank" rel="noopener noreferrer">Open image in new tab</a>
      </> : text !== null ? kind === "markdown" ? <div className="artifact-preview-markdown" role="region" aria-label="Markdown contents" tabIndex={0}>
        <MarkdownContent>{text}</MarkdownContent>
      </div> : <textarea className="artifact-preview-text" aria-label="Plain text contents" value={text} readOnly spellCheck={false} /> : <p role="status">Loading preview…</p>}
    </div>
  </dialog>;
}
