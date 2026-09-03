import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { HUMAN_GATE_DECODER_FIELDS } from "../lib/human-gates.ts";
import { DUPLICATE_HANDLING_DECODER_FIELDS } from "../lib/duplicate-handling.ts";
import { DUPLICATE_SUGGESTION_DECODER_FIELDS } from "../lib/duplicate-suggestions.ts";
import { HIERARCHY_DECODER_FIELDS } from "../lib/hierarchy-presentation.ts";
import { MUTATION_RESPONSE_DECODER_FIELDS } from "../lib/mutation-responses.ts";
import { WORK_EVENT_DECODER_FIELDS } from "../lib/work-events.ts";

const SNAPSHOT_URL = new URL("../../docs/openapi.json", import.meta.url);
const DEFAULTED_RESPONSE_FIELDS = {
  "frontend/lib/human-gates.ts:decodeWorkSummary": [
    "ancestor_path",
    "ancestor_path_truncated"
  ],
  "frontend/lib/duplicate-handling.ts:decodeWorkContext": [
    "incoming_relationships",
    "omitted_relationship_counts",
    "outgoing_relationships",
    "relationship_counts",
    "undirected_relationships"
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
