export type HandoffStatus = "open" | "done" | "wont-do" | "promoted";
export type HandoffCommentKind = "comment" | "work-summary";
export type StatusFilter = HandoffStatus | "all";

export interface Project {
  id: string;
  name: string;
  slug: string;
  description: string;
  repository_url: string | null;
  created_at: string;
  updated_at: string;
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface HandoffSummary {
  id: string;
  project_id: string;
  title: string;
  summary: string;
  status: HandoffStatus;
  source_client: string;
  source_session_id: string;
  source_model: string | null;
  source_session_url: string | null;
  repository_branch: string | null;
  verified_against: string | null;
  tags: string[];
  created_at: string;
  updated_at: string;
  version: number;
}

export interface Handoff extends HandoffSummary {
  prompt: string;
  source_metadata: Record<string, unknown>;
}

export interface HandoffComment {
  id: string;
  handoff_id: string;
  body: string;
  kind: HandoffCommentKind;
  source_client: string;
  source_session_id: string;
  source_model: string | null;
  created_at: string;
}

export interface HandoffCompletion {
  handoff: Handoff;
  comment: HandoffComment;
}

export type HandoffPatch = Partial<Pick<Handoff,
  "title" | "summary" | "prompt" | "status" | "repository_branch" |
  "verified_against" | "tags" | "source_metadata"
>> & { expected_version: number };
