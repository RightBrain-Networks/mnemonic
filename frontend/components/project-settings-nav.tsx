"use client";

import { type MouseEvent, type ReactNode } from "react";
import SidebarNavGroup from "@/components/sidebar-nav-group";
import { dashboardStorageKeys } from "@/lib/dashboard-preferences";
import { settingsSections, type SettingsSection } from "@/lib/settings-navigation";

type Props = {
  section?: SettingsSection;
  icon: ReactNode;
  onNavigate: (event: MouseEvent<HTMLAnchorElement>) => void;
};

const items = settingsSections.map(({ id, label }) => ({ id, label, href: `/settings/${id}` }));

export default function ProjectSettingsNav({ section, icon, onNavigate }: Props) {
  return <SidebarNavGroup className="settings-nav" label="Project settings"
    storageKey={dashboardStorageKeys.settingsMenu} activeId={section}
    items={items} icon={icon} onNavigate={onNavigate} />;
}
