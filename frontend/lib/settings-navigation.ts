export const settingsSections = [
  { id: "workspace", label: "Workspace", description: "Manage the project identity and repository location." },
  { id: "prompts", label: "Prompts", description: "Configure recall pointers and job completion reports for your agents." },
  { id: "code-reviews", label: "Code reviews", description: "Configure the project’s code review policy and instructions." },
  { id: "backups", label: "Backups", description: "Back up and restore the selected project." }
] as const;

export type SettingsSection = typeof settingsSections[number]["id"];
