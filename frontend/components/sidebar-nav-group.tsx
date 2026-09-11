"use client";

import Link from "next/link";
import { useEffect, useId, useState, type MouseEvent, type ReactNode } from "react";

type Props = {
  className: string;
  label: string;
  storageKey: string;
  activeId?: string;
  items: ReadonlyArray<{ id: string; label: string; href: string }>;
  icon: ReactNode;
  onNavigate: (event: MouseEvent<HTMLAnchorElement>) => void;
};

export default function SidebarNavGroup({ className, label, storageKey, activeId, items, icon, onNavigate }: Props) {
  const submenuId = useId();
  const [expanded, setExpanded] = useState(Boolean(activeId));
  const [ready, setReady] = useState(false);

  useEffect(() => {
    try {
      const saved = localStorage.getItem(storageKey);
      if (saved === "open" || saved === "closed") setExpanded(saved === "open");
    } catch { /* The menu remains usable when storage is unavailable. */ }
    setReady(true);
  }, [storageKey]);

  function toggle() {
    const next = !expanded;
    setExpanded(next);
    try {
      localStorage.setItem(storageKey, next ? "open" : "closed");
    } catch { /* Persistence is optional. */ }
  }

  return <div className={`nav-group ${className}`} data-expanded={expanded} data-ready={ready}>
    <button
      type="button"
      className="nav-item nav-group-toggle"
      aria-expanded={expanded}
      aria-controls={submenuId}
      onClick={toggle}
    >
      {icon}<span>{label}</span>
      <svg className="nav-group-chevron" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m9 5 7 7-7 7" /></svg>
    </button>
    <div id={submenuId} className="nav-group-collapse" inert={!expanded} aria-hidden={!expanded}>
      <ul className="nav-group-leaves">
        {items.map(({ id, label, href }) => <li key={id}>
          <Link
            className={`nav-item nav-group-leaf ${activeId === id ? "active" : ""}`}
            href={href}
            aria-current={activeId === id ? "page" : undefined}
            onClick={onNavigate}
          ><span>{label}</span></Link>
        </li>)}
      </ul>
    </div>
  </div>;
}
