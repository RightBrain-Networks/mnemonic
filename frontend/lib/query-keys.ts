// Keys describe requests; UI state and invalidation counters never belong here.
const project = (id: string) => ["mnemonic", "project", id] as const;
const work = (id: string) => [...project(id), "work"] as const;
const tasks = (id: string) => [...project(id), "tasks"] as const;
export const queryKeys = {
  projects: ["mnemonic", "projects"] as const,
  project,
  work,
  tasks,
  settings: (id: string) => [...project(id), "settings"] as const,
  attentionCount: (id: string) => [...project(id), "attention-count"] as const,
  reportCount: (id: string) => [...project(id), "report-count"] as const,
  taskPage: <T extends object>(id: string, filters: T) => [...tasks(id), "page", filters] as const,
  workQueue: <T extends object>(id: string, filters: T) => [...work(id), "queue", filters] as const,
  canonicalWork: <T extends object>(id: string, filters: T) => [...work(id), "canonical", filters] as const
};
