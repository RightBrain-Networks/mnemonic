/** Static-only fixtures: completion-evidence wire and input types stay closed to extras. */

import type {
  ArtifactReferenceInput,
  ArtifactReferenceRead,
  CommandVerificationInput,
  CompletionEvidenceEpisodeRead,
  CompletionEvidenceInput,
  CompletionEvidencePage,
  CompletionEvidencePayloadRead,
  ObservationVerificationInput,
  VerificationResultRead
} from "../../lib/types.ts";

declare const unexpectedCompletionEvidenceValue: never;

const commandInput: CommandVerificationInput = {
  verification_type: "command",
  name: "Frontend typecheck",
  outcome: "passed",
  summary: "The focused check passed.",
  command: "npm run typecheck",
  exit_code: 0,
  // @ts-expect-error completion-evidence command results are exact wire objects.
  unexpected_completion_evidence_field: unexpectedCompletionEvidenceValue
};

const observationInput: ObservationVerificationInput = {
  verification_type: "observation",
  name: "Frontend review",
  outcome: "passed",
  summary: "The completion evidence was reviewed.",
  // @ts-expect-error completion-evidence observations are exact wire objects.
  unexpected_completion_evidence_field: unexpectedCompletionEvidenceValue
};

const artifactInput: ArtifactReferenceInput = {
  artifact_type: "commit",
  label: "Reviewed commit",
  reference: "abcdef1",
  // @ts-expect-error completion-evidence artifacts are exact wire objects.
  unexpected_completion_evidence_field: unexpectedCompletionEvidenceValue
};

const evidenceInput: CompletionEvidenceInput = {
  verification_results: [],
  artifact_references: [],
  // @ts-expect-error completion-evidence input accepts only its two arrays.
  unexpected_completion_evidence_field: unexpectedCompletionEvidenceValue
};

const verificationRead: VerificationResultRead = {
  verification_type: "observation",
  name: "Frontend review",
  outcome: "passed",
  summary: "The completion evidence was reviewed.",
  id: "670bdf0e-3ae5-4cff-b38a-0e0f2cff8d02",
  work_item_id: "20000000-0000-0000-0000-000000000001",
  completion_checkpoint_id: "30000000-0000-0000-0000-000000000001",
  position: 0,
  created_at: "2026-09-04T12:00:00Z",
  // @ts-expect-error completion-evidence result reads are exact wire objects.
  unexpected_completion_evidence_field: unexpectedCompletionEvidenceValue
};

const artifactRead: ArtifactReferenceRead = {
  artifact_type: "commit",
  label: "Reviewed commit",
  reference: "abcdef1",
  id: "939698f5-33aa-4210-bbc6-91df2799b2c7",
  work_item_id: "20000000-0000-0000-0000-000000000001",
  completion_checkpoint_id: "30000000-0000-0000-0000-000000000001",
  position: 0,
  created_at: "2026-09-04T12:00:00Z",
  // @ts-expect-error completion-evidence artifact reads are exact wire objects.
  unexpected_completion_evidence_field: unexpectedCompletionEvidenceValue
};

const evidencePayload: CompletionEvidencePayloadRead = {
  verification_results: [],
  artifact_references: [],
  // @ts-expect-error completion-evidence response payloads are exact wire objects.
  unexpected_completion_evidence_field: unexpectedCompletionEvidenceValue
};

declare const completionCheckpoint: CompletionEvidenceEpisodeRead["completion_checkpoint"];

const evidenceEpisode: CompletionEvidenceEpisodeRead = {
  completion_event_id: "1",
  completion_checkpoint: completionCheckpoint,
  verification_results: [],
  artifact_references: [],
  // @ts-expect-error completion-evidence history episodes are exact wire objects.
  unexpected_completion_evidence_field: unexpectedCompletionEvidenceValue
};

const evidencePage: CompletionEvidencePage = {
  work_item_id: "20000000-0000-0000-0000-000000000001",
  work_version: 1,
  lifecycle_status: "done",
  is_duplicate: false,
  canonical_work_item_id: "20000000-0000-0000-0000-000000000001",
  current_completion_checkpoint_id: null,
  as_of_completion_event_id: null,
  items: [],
  total: 0,
  structured_completion_total: 0,
  limit: 10,
  next_cursor: null,
  // @ts-expect-error completion-evidence pages are exact wire objects.
  unexpected_completion_evidence_field: unexpectedCompletionEvidenceValue
};

void commandInput;
void observationInput;
void artifactInput;
void evidenceInput;
void verificationRead;
void artifactRead;
void evidencePayload;
void evidenceEpisode;
void evidencePage;
