"use client";

import { useEffect, useId, useState, type MouseEvent, type ReactNode } from "react";
import { dashboardStorageKeys } from "@/lib/dashboard-preferences";
import { settingsSections, type SettingsSection } from "@/lib/settings-navigation";

type Props = {
  section?: SettingsSection;
  icon: ReactNode;
  onNavigate: (event: MouseEvent<HTMLAnchorElement>) => void;
};

export default function ProjectSettingsNav({ section, icon, onNavigate }: Props) {
  const submenuId = useId();
  const [expanded, setExpanded] = useState(Boolean(section));
  const [ready, setReady] = useState(false);

  useEffect(() => {
    try {
      const saved = localStorage.getItem(dashboardStorageKeys.settingsMenu);
      if (saved === "open" || saved === "closed") setExpanded(saved === "open");
    } catch { /* The menu remains usable when storage is unavailable. */ }
    setReady(true);
  }, []);

  function toggle() {
    const next = !expanded;
    setExpanded(next);
    try {
      localStorage.setItem(dashboardStorageKeys.settingsMenu, next ? "open" : "closed");
    } catch { /* Persistence is optional. */ }
  }

  return <div className="settings-nav" data-expanded={expanded} data-ready={ready}>
    <button
      type="button"
      className="nav-item settings-nav-toggle"
      aria-expanded={expanded}
      aria-controls={submenuId}
      onClick={toggle}
    >
      {icon}<span>Project settings</span>
      <svg className="settings-nav-chevron" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m9 5 7 7-7 7" /></svg>
    </button>
    <div id={submenuId} className="settings-nav-collapse" inert={!expanded} aria-hidden={!expanded}>
      <ul className="settings-nav-leaves">
        {settingsSections.map(({ id, label }) => <li key={id}>
          <a
            className={`nav-item settings-nav-leaf ${section === id ? "active" : ""}`}
            href={`/settings/${id}`}
            aria-current={section === id ? "page" : undefined}
            onClick={onNavigate}
          ><span>{label}</span></a>
        </li>)}
      </ul>
    </div>
  </div>;
}
