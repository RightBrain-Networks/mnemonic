"use client";

import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import MarkdownContent from "@/components/markdown-content";

/** Shared accessible drawer for retained decisions and artifact previews. */
export default function DocumentPreviewDrawer({ title, label, text, copyable = true, className = "", children, onClose }: {
  title: string; label: string; text: string | null; copyable?: boolean; className?: string;
  children: ReactNode; onClose: () => void;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const titleId = useId();
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
  useEffect(() => { setCopyStatus(""); }, [text]);

  async function copyContents() {
    if (text === null) return;
    try { await navigator.clipboard.writeText(text); setCopyStatus("Contents copied."); }
    catch { setCopyStatus("Unable to copy. Select the contents and copy them manually."); }
  }

  return <dialog ref={dialogRef} className={`artifact-preview-drawer ${className}`.trim()} aria-labelledby={titleId}
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
      <div><span className="section-label">{label}</span><h2 id={titleId} title={title}>{title}</h2></div>
      <div className="artifact-preview-actions">
        {copyable && <button type="button" className="icon-button" aria-label="Copy contents" title="Copy contents" disabled={text === null} onClick={() => void copyContents()}>
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true"><rect x="8" y="8" width="12" height="13" rx="2" /><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3" /></svg>
        </button>}
        <button type="button" className="icon-button" aria-label="Close preview" title="Close preview (Esc)" autoFocus onClick={onClose}>
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18" /></svg>
        </button>
      </div>
    </header>
    {copyStatus && <p className="artifact-preview-notice" role="status">{copyStatus}</p>}
    {children}
  </dialog>;
}

export function DocumentPreviewText({ text, markdown, label = "Plain text contents" }: { text: string; markdown: boolean; label?: string }) {
  return markdown ? <div className="artifact-preview-markdown" role="region" aria-label="Markdown contents" tabIndex={0}>
    <MarkdownContent>{text}</MarkdownContent>
  </div> : <textarea className="artifact-preview-text" aria-label={label} value={text} readOnly spellCheck={false} />;
}
