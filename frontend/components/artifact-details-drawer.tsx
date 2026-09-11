"use client";

import { useEffect, useId, useRef, type ReactNode } from "react";
import { formatArtifactSize, type Artifact } from "@/lib/artifacts";

export default function ArtifactDetailsDrawer({ artifact, pending, onClose, children }: {
  artifact: Artifact;
  pending: boolean;
  onClose: () => void;
  children: ReactNode;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const titleId = useId();

  useEffect(() => {
    const dialog = dialogRef.current!;
    const returnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    dialog.showModal();
    return () => {
      dialog.close();
      if (returnFocus?.isConnected) returnFocus.focus({ preventScroll: true });
    };
  }, []);

  return <dialog ref={dialogRef} className="artifact-details-drawer" aria-labelledby={titleId}
    onCancel={(event) => { event.preventDefault(); if (!pending) onClose(); }}
    onDragEnter={(event) => { event.preventDefault(); event.stopPropagation(); }}
    onDragOver={(event) => { event.preventDefault(); event.stopPropagation(); event.dataTransfer.dropEffect = "none"; }}
    onDrop={(event) => { event.preventDefault(); event.stopPropagation(); }}
    onClick={(event) => {
      if (pending || event.target !== event.currentTarget) return;
      const bounds = event.currentTarget.getBoundingClientRect();
      if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) onClose();
    }}>
    <section className="artifact-details" aria-label={`Metadata for ${artifact.filename}`}>
      <header className="artifact-details-header">
        <div className="artifact-detail-heading">
          <span className="section-label">Artifact details</span>
          <button type="button" className="icon-button" aria-label="Close details" title="Close details (Esc)" autoFocus disabled={pending} onClick={onClose}>
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18" /></svg>
          </button>
        </div>
        <h2 id={titleId}>{artifact.filename}</h2>
        <p className="artifact-detail-summary"><span>{artifact.mime_type || "Unknown file type"}</span><span>{formatArtifactSize(artifact.size_bytes)}</span><span className="artifact-revision">r{artifact.revision}</span>{artifact.deleted_at && <span>Deleted</span>}</p>
      </header>
      <div className="artifact-details-body">{children}</div>
    </section>
  </dialog>;
}
