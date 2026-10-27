export const settingsSections = [
  { id: "workspace", label: "Workspace", description: "Manage project details, leases, priorities, and automatic decision impact reviews." },
  { id: "tags", label: "Tags", description: "Manage the tags used to organize and filter work items." },
  { id: "prompts", label: "Prompts", description: "Customize every project prompt sent to your agents." },
  { id: "code-reviews", label: "Code reviews", description: "Configure the project’s code review policy." },
  { id: "backups", label: "Backups", description: "Back up and restore the selected project." }
] as const;

export type SettingsSection = typeof settingsSections[number]["id"];
