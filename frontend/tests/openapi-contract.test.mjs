import { EXTERNAL_REFERENCE_DECODER_FIELDS } from "../lib/external-references.ts";
import { JOB_REPORT_DECODER_FIELDS } from "../lib/job-completion-reports.ts";
import { PROJECT_ACTIVITY_DECODER_FIELDS } from "../lib/project-activity.ts";
import { WORK_DECODER_FIELDS } from "../lib/work-codecs.ts";
import { REVISION_DECODER_FIELDS } from "../lib/revision-codecs.ts";
import { READINESS_DECODER_FIELDS } from "../lib/readiness-codecs.ts";
import { RELATIONSHIP_DECODER_FIELDS } from "../lib/relationship-codecs.ts";
import { CHECKPOINT_DECODER_FIELDS } from "../lib/checkpoint-codecs.ts";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { HUMAN_GATE_DECODER_FIELDS } from "../lib/human-gates.ts";
import { DUPLICATE_HANDLING_DECODER_FIELDS } from "../lib/duplicate-handling.ts";
import { DUPLICATE_SUGGESTION_DECODER_FIELDS } from "../lib/duplicate-suggestions.ts";
import { HIERARCHY_DECODER_FIELDS } from "../lib/hierarchy-presentation.ts";
import { MUTATION_RESPONSE_DECODER_FIELDS } from "../lib/mutation-responses.ts";
import { WORK_EVENT_DECODER_FIELDS } from "../lib/work-events.ts";
import { COMPLETION_EVIDENCE_DECODER_FIELDS } from "../lib/completion-evidence.ts";

const SNAPSHOT_URL = new URL("../../docs/openapi.json", import.meta.url);
const DEFAULTED_RESPONSE_FIELDS = {
  "frontend/lib/readiness-codecs.ts:decodeLease": ["purpose", "code_review_id", "mode"],
  "frontend/lib/duplicate-handling.ts:decodeWorkItemDetail": ["code_review_context"],
  "frontend/lib/work-events.ts:EVENT_FIELDS": ["code_review_id", "code_review_result_id", "work_follow_up_id", "work_follow_up_answer_id"],
  "frontend/lib/external-references.ts:decodeExternalReference": ["label", "state_observed_at"],
  "frontend/lib/work-codecs.ts:decodeWorkItem": ["external_references"],
  "frontend/lib/duplicate-handling.ts:decodeWorkPointer": ["external_references"],
  "frontend/lib/duplicate-suggestions.ts:decodeDuplicateCandidateSummary": ["external_references"],
  "frontend/lib/duplicate-suggestions.ts:decodeDuplicateSuggestionPage": ["external_items", "external_candidate_count", "external_scope"],
  "frontend/lib/checkpoint-codecs.ts:decodeCheckpoint": [
    "affected_paths"
  ],
  "frontend/lib/mutation-responses.ts:decodeMutationResult:complete_work": [
    "completion_evidence", "job_completion_report", "review_policy_decision", "code_review_request", "agent_follow_ups", "code_review_handoff"
  ],
  "frontend/lib/completion-evidence.ts:decodeVerificationResult:command": [
    "exit_code", "observed_at", "observed_at_commit"
  ],
  "frontend/lib/completion-evidence.ts:decodeVerificationResult:observation": [
    "observed_at", "observed_at_commit"
  ],
  "frontend/lib/work-codecs.ts:decodeWorkSummary": [
    "ancestor_path",
    "ancestor_path_truncated"
  ],
  "frontend/lib/duplicate-handling.ts:decodeWorkContext": [
    "incoming_relationships",
    "omitted_relationship_counts",
    "outgoing_relationships",
    "relationship_counts",
    "undirected_relationships",
    "code_review_context"
  ]
};

function qualifiedFields(source, propertySets) {
  return Object.fromEntries(Object.entries(propertySets).map(([name, fields]) => [
    `${source}:${name}`,
    fields
  ]));
}

function sorted(values) {
  return [...values].sort();
}

test("strict frontend decoders match the committed OpenAPI component contracts", async () => {
  const document = JSON.parse(await readFile(SNAPSHOT_URL, "utf8"));
  const mappedSchemas = document["x-mnemonic-schema-consumers"]?.frontend?.property_sets;
  const schemas = document.components?.schemas;
  assert.ok(mappedSchemas, "OpenAPI snapshot must declare frontend property-set mappings");
  assert.ok(schemas, "OpenAPI snapshot must declare component schemas");

  const decoderFields = {
    ...qualifiedFields("frontend/lib/external-references.ts", EXTERNAL_REFERENCE_DECODER_FIELDS),
    ...qualifiedFields("frontend/lib/job-completion-reports.ts", JOB_REPORT_DECODER_FIELDS),
    ...qualifiedFields("frontend/lib/project-activity.ts", PROJECT_ACTIVITY_DECODER_FIELDS),
    ...qualifiedFields("frontend/lib/work-codecs.ts", WORK_DECODER_FIELDS),
    ...qualifiedFields("frontend/lib/revision-codecs.ts", REVISION_DECODER_FIELDS),
    ...qualifiedFields("frontend/lib/readiness-codecs.ts", READINESS_DECODER_FIELDS),
    ...qualifiedFields("frontend/lib/relationship-codecs.ts", RELATIONSHIP_DECODER_FIELDS),
    ...qualifiedFields("frontend/lib/checkpoint-codecs.ts", CHECKPOINT_DECODER_FIELDS),
    ...qualifiedFields(
      "frontend/lib/completion-evidence.ts",
      COMPLETION_EVIDENCE_DECODER_FIELDS
    ),
    ...qualifiedFields("frontend/lib/human-gates.ts", HUMAN_GATE_DECODER_FIELDS),
    ...qualifiedFields("frontend/lib/duplicate-handling.ts", DUPLICATE_HANDLING_DECODER_FIELDS),
    ...qualifiedFields("frontend/lib/duplicate-suggestions.ts", DUPLICATE_SUGGESTION_DECODER_FIELDS),
    ...qualifiedFields("frontend/lib/hierarchy-presentation.ts", HIERARCHY_DECODER_FIELDS),
    ...qualifiedFields("frontend/lib/mutation-responses.ts", MUTATION_RESPONSE_DECODER_FIELDS),
    ...qualifiedFields("frontend/lib/work-events.ts", WORK_EVENT_DECODER_FIELDS)
  };
  assert.deepEqual(
    sorted(Object.keys(decoderFields)),
    sorted(Object.keys(mappedSchemas)),
    "frontend decoder mappings must cover the same consumers as the snapshot"
  );

  for (const [consumer, expectedFields] of Object.entries(decoderFields)) {
    const schemaName = mappedSchemas[consumer];
    const schema = schemas[schemaName];
    assert.ok(schema, `${consumer} must map to an existing component schema`);
    assert.deepEqual(
      sorted(Object.keys(schema.properties ?? {})),
      sorted(expectedFields),
      `${consumer} properties drifted from ${schemaName}`
    );

    const defaultedFields = DEFAULTED_RESPONSE_FIELDS[consumer] ?? [];
    assert.ok(
      defaultedFields.every((field) => expectedFields.includes(field)),
      `${consumer} declares an unknown defaulted response field`
    );
    assert.deepEqual(
      sorted(schema.required ?? []),
      sorted(expectedFields.filter((field) => !defaultedFields.includes(field))),
      `${consumer} required fields drifted from ${schemaName}`
    );
  }
});
