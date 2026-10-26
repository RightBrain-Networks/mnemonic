"use client";

import { useId, useState, type ReactNode } from "react";
import MarkdownContent from "@/components/markdown-content";

type Props = {
  storageKey: string;
  description: ReactNode;
  label: string;
  children: string;
};

/** Give each help topic a stable storage key, independent of the current project. */
export default function InfoContainer(props: Props) {
  return <InfoDisclosure key={props.storageKey} {...props} />;
}

function InfoDisclosure({ storageKey, description, label, children }: Props) {
  const contentId = useId();
  const [expanded, setExpanded] = useState(() => {
    try { return localStorage.getItem(storageKey) === "open"; }
    catch { return false; }
  });

  function toggle() {
    const next = !expanded;
    setExpanded(next);
    try { localStorage.setItem(storageKey, next ? "open" : "closed"); }
    catch { /* Help remains usable when browser storage is unavailable. */ }
  }

  return <div className="info-container">
    <p className="info-container-description">
      {description}{" "}
      <button
        type="button"
        className="info-container-toggle"
        aria-label={label}
        aria-expanded={expanded}
        aria-controls={contentId}
        title={`${expanded ? "Hide" : "Show"} ${label.toLowerCase()}`}
        onClick={toggle}
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <circle cx="12" cy="12" r="9" />
          <path d="M9.5 9a2.5 2.5 0 0 1 5 .5c0 1.5-2.5 1.5-2.5 3" />
          <path d="M12 16h.01" />
        </svg>
      </button>
    </p>
    <div id={contentId} className="info-container-content" role="region" aria-label={label} hidden={!expanded}>
      <MarkdownContent>{children}</MarkdownContent>
    </div>
  </div>;
}
