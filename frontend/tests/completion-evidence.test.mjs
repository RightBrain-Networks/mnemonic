import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import {
  COMPLETION_EVIDENCE_MAX_BYTES,
  COMPLETION_EVIDENCE_RESPONSE_MAX_BYTES,
  COMPLETION_WORK_VERSION_MAX,
  IdentityEvidenceResponseError,
  MAX_COMPLETION_EVENT_ID,
  artifactNavigationHref,
  canonicalObservedAt,
  completionEvidenceAggregateBytes,
  completionEvidenceDraftIssues,
  completionEvidenceFromDraft,
  completionEvidenceIssues,
  decodeCompletionEvidencePage,
  decodeCompletionEvidencePayload,
  decodeIdentityEvidenceJson,
  identityContentEncoding,
  mergeCompletionEvidencePage,
  normalizeCompletionEvidenceInput,
  readIdentityEvidenceBytes,
  validExternalArtifactUrl
} from "../lib/completion-evidence.ts";
import { invalidMutationBody } from "../lib/proxy-policy.ts";

const fixtureUrl = new URL("../../tests/fixtures/completion-evidence-v1.json", import.meta.url);
const fixture = JSON.parse(await readFile(fixtureUrl, "utf8"));
const work = "7a5dc555-0a6d-4f92-9678-1647524827c8";
const canonical = "7a5dc555-0a6d-4f92-9678-1647524827c8";
const checkpoint = "1dfa9455-4a17-4cd4-938b-010ea17ccaf0";
const olderCheckpoint = "f1cf3691-7d28-4716-94a9-4867b341a685";
const resultId = "26a3a437-0af3-405a-ab82-7932d17869e0";
const artifactId = "11111111-1111-4111-8111-111111111111";
const createdAt = "2026-09-03T18:04:12.123456Z";

test("frontend semantic validation consumes every shared Phase 11 corpus case", () => {
  assert.equal(fixture.contract_version, 1);
  for (const entry of fixture.cases) {
    if (entry.valid) {
      assert.deepEqual(
        normalizeCompletionEvidenceInput(entry.semantic_input),
        entry.canonical_output,
        entry.case_id
      );
      continue;
    }
    const issues = completionEvidenceIssues(entry.semantic_input);
    assert.ok(issues.length > 0, entry.case_id);
    assert.deepEqual(issues[0].path, entry.error_path, entry.case_id);
    assert.equal(issues[0].errorClass, entry.error_class, entry.case_id);
    assert.throws(
      () => normalizeCompletionEvidenceInput(entry.semantic_input),
      (error) => error.issue?.errorClass === entry.error_class,
      entry.case_id
    );
  }
});

test("frontend completion proxy consumes every shared full-request case", () => {
  const concreteCases = new Map(fixture.cases.map((entry) => [entry.case_id, entry]));
  const path = "projects/e36a7e53-938f-4c8a-b75a-af9c7331711a/work-items/"
    + "7a5dc555-0a6d-4f92-9678-1647524827c8/complete";
  for (const entry of fixture.full_request_cases) {
    const body = {
      expected_version: entry.expected_version ?? 1,
      checkpoint: {
        prompt: "Complete.",
        source_client: "dashboard",
        source_session_id: "shared-request-corpus"
      }
    };
    if (entry.completion_evidence_case_id) {
      body.completion_evidence = structuredClone(
        concreteCases.get(entry.completion_evidence_case_id).semantic_input
      );
    } else if (
      Object.hasOwn(entry, "completion_evidence")
      && entry.completion_evidence !== "__omitted__"
    ) {
      body.completion_evidence = structuredClone(entry.completion_evidence);
    }
    if (entry.client_operation_id !== "__omitted__") {
      body.client_operation_id = entry.client_operation_id;
    }
    assert.equal(
      invalidMutationBody(path, "POST", body) === null,
      entry.surface_expectations.browser,
      entry.case_id
    );
  }
});

function observations(count, summary = "x") {
  return {
    verification_results: Array.from({ length: count }, (_, index) => ({
      verification_type: "observation",
      name: `Observation ${index}`,
      outcome: "passed",
      summary
    }))
  };
}

function aggregateAt(target) {
  const results = [...Array(8).fill(4_000), 1].map((length, index) => ({
    verification_type: "observation",
    name: "n",
    outcome: "passed",
    summary: "x".repeat(length),
    index
  }));
  const overhead = results.reduce((total, result) => (
    total
      + Buffer.byteLength(result.verification_type)
      + Buffer.byteLength(result.name)
      + Buffer.byteLength(result.outcome)
  ), 0);
  const remaining = target - overhead;
  results[8].summary = "x".repeat(remaining - 32_000);
  return {
    verification_results: results.map(({ index: _index, ...result }) => result)
  };
}

test("generated shared boundaries enforce entry, scalar, UTF-8, and aggregate limits", () => {
  const byId = Object.fromEntries(fixture.generated_boundaries.map((entry) => [
    entry.case_id,
    entry
  ]));
  assert.equal(completionEvidenceIssues(observations(byId.twenty_total_entries.count)).length, 0);
  assert.equal(
    completionEvidenceIssues(observations(byId.twenty_one_total_entries.count))[0].errorClass,
    "count"
  );

  const exact = normalizeCompletionEvidenceInput(aggregateAt(byId.aggregate_32768_bytes.bytes));
  assert.equal(completionEvidenceAggregateBytes(exact), COMPLETION_EVIDENCE_MAX_BYTES);
  const overIssues = completionEvidenceIssues(aggregateAt(byId.aggregate_32769_bytes.bytes));
  assert.equal(overIssues[0].errorClass, "aggregate_bytes");

  const unicode = observations(1, "\u{10000}".repeat(
    byId.unicode_scalar_and_utf8_boundaries.characters
  ));
  assert.equal(completionEvidenceIssues(unicode).length, 0);
  assert.equal(
    Buffer.byteLength(unicode.verification_results[0].summary),
    byId.unicode_scalar_and_utf8_boundaries.bytes
  );
});

test("editor drafts preserve order and spelling while canonicalizing only timestamps", () => {
  assert.equal(completionEvidenceFromDraft({
    verificationResults: [],
    artifactReferences: []
  }), null);
  const draft = {
    verificationResults: [{
      key: "result-1",
      verificationType: "command",
      name: "Browser tests",
      outcome: "passed",
      summary: "The browser suite passed.",
      command: "npm test",
      exitCode: "0",
      observedAt: "2026-09-03T14:01:02-04:00",
      observedAtCommit: "7ad62e4"
    }],
    artifactReferences: [{
      key: "artifact-1",
      artifactType: "branch",
      label: "Exact branch",
      reference: "work/Phase\u001c11"
    }]
  };
  assert.deepEqual(completionEvidenceDraftIssues(draft), []);
  assert.deepEqual(completionEvidenceFromDraft(draft), {
    verification_results: [{
      verification_type: "command",
      name: "Browser tests",
      outcome: "passed",
      summary: "The browser suite passed.",
      command: "npm test",
      exit_code: 0,
      observed_at: "2026-09-03T18:01:02Z",
      observed_at_commit: "7ad62e4"
    }],
    artifact_references: [{
      artifact_type: "branch",
      label: "Exact branch",
      reference: "work/Phase\u001c11"
    }]
  });

  const invalid = structuredClone(draft);
  invalid.verificationResults[0].outcome = "inconclusive";
  assert.deepEqual(
    completionEvidenceDraftIssues(invalid)[0].path,
    ["completion_evidence", "verification_results", 0, "exit_code"]
  );

  const negativeZero = structuredClone(draft);
  negativeZero.verificationResults[0].exitCode = "-0";
  const negativeZeroIssue = completionEvidenceDraftIssues(negativeZero)[0];
  assert.deepEqual(
    negativeZeroIssue.path,
    ["completion_evidence", "verification_results", 0, "exit_code"]
  );
  assert.equal(negativeZeroIssue.errorClass, "type");
  assert.match(negativeZeroIssue.message, /negative zero cannot be preserved/);
  assert.throws(
    () => completionEvidenceFromDraft(negativeZero),
    (error) => error.issue?.errorClass === "type"
  );
});

test("timestamp canonicalization is calendar-aware, finite, and independent of Date parsing", () => {
  assert.equal(
    canonicalObservedAt("2026-09-03T14:01:02-04:00"),
    "2026-09-03T18:01:02Z"
  );
  assert.equal(
    canonicalObservedAt("2026-01-01T00:30:00+01:00"),
    "2025-12-31T23:30:00Z"
  );
  assert.equal(
    canonicalObservedAt("2024-02-29T23:30:00-01:00"),
    "2024-03-01T00:30:00Z"
  );
  assert.equal(canonicalObservedAt("0001-01-01T00:00:00Z"), "0001-01-01T00:00:00Z");
  assert.equal(
    canonicalObservedAt("9999-12-31T23:59:59.999999Z"),
    "9999-12-31T23:59:59.999999Z"
  );
  for (const value of [
    "0001-01-01T00:00:00+00:01",
    "9999-12-31T23:59:59-00:01",
    "2026-02-29T00:00:00Z",
    "2026-01-01T00:00:00+14:01",
    "2026-01-01T00:00:00-00:00",
    "2026-01-01t00:00:00Z",
    "2026-01-01T00:00:00.0000000Z"
  ]) assert.equal(canonicalObservedAt(value), null, value);
});

test("external artifact URLs are exact-preserving ASCII HTTPS locators only", () => {
  for (const value of [
    "https://example.test/path",
    "https://example.test:8443/path",
    "https://127.0.0.1/path",
    "https://[::1]/path"
  ]) assert.equal(validExternalArtifactUrl(value), true, value);
  for (const value of [
    "http://example.test/path",
    "HTTPS://example.test/path",
    "https://EXAMPLE.test/path",
    "https://example.test",
    "https://example.test:443/path",
    "https://user@example.test/path",
    "https://example.test/path?token=no",
    "https://example.test/path#fragment",
    "https://example.test/a/../b",
    "https://example.test/a%2fb",
    "https://example.test/a%4a",
    "https://example.test/a%7e",
    "https://example.test/white space",
    "https://éxample.test/path"
  ]) assert.equal(validExternalArtifactUrl(value), false, value);
  assert.equal(artifactNavigationHref({
    artifact_type: "pull_request",
    label: "PR",
    reference: "https://example.test/pull/1"
  }), "https://example.test/pull/1");
  assert.equal(artifactNavigationHref({
    artifact_type: "branch",
    label: "Branch",
    reference: "work/phase11"
  }), null);
});

function pointer(id = checkpoint) {
  return {
    id,
    work_item_id: work,
    kind: "completion",
    source_client: "dashboard",
    source_session_id: "tab-1",
    source_model: null,
    repository_branch: "work/phase11",
    verified_against: "7ad62e4",
    tags: ["frontend"],
    migration_origin: null,
    legacy_record_id: null,
    created_at: createdAt
  };
}

function verification(overrides = {}) {
  return {
    id: resultId,
    work_item_id: work,
    completion_checkpoint_id: checkpoint,
    position: 0,
    verification_type: "command",
    name: "Frontend tests",
    outcome: "passed",
    summary: "The frontend unit suite passed.",
    command: "npm test",
    exit_code: 0,
    observed_at: "2026-09-03T18:01:02Z",
    observed_at_commit: "7ad62e4",
    created_at: createdAt,
    ...overrides
  };
}

function artifact(overrides = {}) {
  return {
    id: artifactId,
    work_item_id: work,
    completion_checkpoint_id: checkpoint,
    position: 0,
    artifact_type: "pull_request",
    label: "Phase 11 pull request",
    reference: "https://example.test/pull/11",
    created_at: createdAt,
    ...overrides
  };
}

function episode(eventId, overrides = {}) {
  return {
    completion_event_id: eventId,
    completion_checkpoint: pointer(),
    verification_results: [],
    artifact_references: [],
    ...overrides
  };
}

function page(overrides = {}) {
  return {
    work_item_id: work,
    work_version: 8,
    lifecycle_status: "done",
    is_duplicate: false,
    canonical_work_item_id: canonical,
    current_completion_checkpoint_id: checkpoint,
    as_of_completion_event_id: MAX_COMPLETION_EVENT_ID.toString(),
    items: [
      episode(MAX_COMPLETION_EVENT_ID.toString(), {
        verification_results: [verification()],
        artifact_references: [artifact()]
      }),
      episode("9007199254740992", {
        completion_checkpoint: pointer(olderCheckpoint)
      })
    ],
    total: 2,
    structured_completion_total: 1,
    limit: 10,
    next_cursor: null,
    ...overrides
  };
}

test("strict history decoding retains bigint identities as strings and ordered empty episodes", () => {
  const decoded = decodeCompletionEvidencePage(page(), work);
  assert.deepEqual(
    decoded.items.map((entry) => entry.completion_event_id),
    [MAX_COMPLETION_EVENT_ID.toString(), "9007199254740992"]
  );
  assert.equal(typeof decoded.items[0].completion_event_id, "string");
  assert.deepEqual(decoded.items[1].verification_results, []);
  assert.deepEqual(decoded.items[1].artifact_references, []);
  assert.equal(decoded.items[0].verification_results[0].summary, "The frontend unit suite passed.");
  assert.equal(decoded.items[0].artifact_references[0].reference, "https://example.test/pull/11");
});

test("history decoding enforces the backend work-version maximum", () => {
  const maximum = decodeCompletionEvidencePage(page({
    work_version: COMPLETION_WORK_VERSION_MAX
  }), work);
  assert.equal(maximum.work_version, COMPLETION_WORK_VERSION_MAX);
  assert.throws(
    () => decodeCompletionEvidencePage(page({
      work_version: COMPLETION_WORK_VERSION_MAX + 1
    }), work),
    /invalid completion-evidence page/i
  );
});

test("server-owned evidence timestamps require canonical UTC seconds or six microseconds", () => {
  const pageAt = (timestamp) => page({
    items: [episode(MAX_COMPLETION_EVENT_ID.toString(), {
      completion_checkpoint: { ...pointer(), created_at: timestamp },
      verification_results: [verification({ created_at: timestamp })],
      artifact_references: [artifact({ created_at: timestamp })]
    })],
    total: 1,
    structured_completion_total: 1
  });
  const payloadAt = (timestamp, child) => ({
    verification_results: child === "verification"
      ? [verification({ created_at: timestamp })]
      : [],
    artifact_references: child === "artifact"
      ? [artifact({ created_at: timestamp })]
      : []
  });

  for (const timestamp of (
    ["2026-09-03T18:04:12Z", "2026-09-03T18:04:12.123456Z"]
  )) {
    assert.equal(
      decodeCompletionEvidencePage(pageAt(timestamp), work).items[0]
        .completion_checkpoint.created_at,
      timestamp
    );
    for (const child of ["verification", "artifact"]) {
      assert.doesNotThrow(() => decodeCompletionEvidencePayload(
        payloadAt(timestamp, child), work, checkpoint, timestamp
      ));
    }
  }

  for (const timestamp of (
    [
      "2026-09-03T18:04:12.1Z",
      "2026-09-03T18:04:12.0000000Z",
      "2026-09-03T18:04:12+00:00",
      "2026-02-30T18:04:12Z"
    ]
  )) {
    assert.throws(
      () => decodeCompletionEvidencePage(pageAt(timestamp), work),
      /invalid completion evidence/i,
      timestamp
    );
    for (const child of ["verification", "artifact"]) {
      assert.throws(
        () => decodeCompletionEvidencePayload(
          payloadAt(timestamp, child), work, checkpoint, timestamp
        ),
        /invalid completion evidence/i,
        `${child}: ${timestamp}`
      );
    }
  }
});

test("fresh-head current identity is newest while continuations may expose newer live facts", () => {
  const otherCheckpoint = "f1cf3691-7d28-4716-94a9-4867b341a685";
  assert.throws(() => decodeCompletionEvidencePage(page({
    current_completion_checkpoint_id: otherCheckpoint
  }), work));
  assert.throws(() => decodeCompletionEvidencePage(page({
    items: [],
    total: 2
  }), work));

  const continuation = decodeCompletionEvidencePage(page({
    current_completion_checkpoint_id: otherCheckpoint,
    items: [episode("9007199254740992")]
  }), work, false);
  assert.equal(continuation.current_completion_checkpoint_id, otherCheckpoint);
  assert.equal(decodeCompletionEvidencePage(page({
    current_completion_checkpoint_id: null,
    items: [],
    total: 2
  }), work, false).items.length, 0);

  const pagedHead = decodeCompletionEvidencePage(page({
    items: [page().items[0]],
    total: 2,
    limit: 1,
    next_cursor: "AA"
  }), work);
  assert.equal(pagedHead.next_cursor, "AA");
  assert.throws(() => decodeCompletionEvidencePage(page({
    items: [page().items[0]],
    total: 2,
    limit: 1,
    next_cursor: null
  }), work));

  assert.equal(decodeCompletionEvidencePage(page({
    current_completion_checkpoint_id: null
  }), work).current_completion_checkpoint_id, null);
  assert.equal(decodeCompletionEvidencePage(page({
    is_duplicate: true,
    canonical_work_item_id: otherCheckpoint,
    current_completion_checkpoint_id: null
  }), work).is_duplicate, true);
});

test("history decoding rejects widened pages, unsafe bigint identities, and child incoherence", () => {
  const secondCheckpoint = "f1cf3691-7d28-4716-94a9-4867b341a685";
  const cases = [
    page({ private_generation: 1 }),
    page({ as_of_completion_event_id: "092" }),
    page({ as_of_completion_event_id: "9223372036854775807" }),
    page({ current_completion_checkpoint_id: checkpoint, lifecycle_status: "pending" }),
    page({ canonical_work_item_id: "f1cf3691-7d28-4716-94a9-4867b341a685" }),
    page({ is_duplicate: true, current_completion_checkpoint_id: null }),
    page({ next_cursor: "not+base64" }),
    page({ next_cursor: "A" }),
    page({ next_cursor: "A".repeat(2_732) }),
    page({ next_cursor: "AA" }),
    page({ structured_completion_total: 0 }),
    page({
      as_of_completion_event_id: "100",
      items: [episode("99")],
      total: 1,
      structured_completion_total: 0
    }),
    page({
      items: [page().items[0], episode("9007199254740992")],
      structured_completion_total: 2
    }),
    page({
      items: [page().items[0], episode("9007199254740992", {
        completion_checkpoint: pointer(checkpoint)
      })]
    }),
    page({
      items: [
        page().items[0],
        episode("9007199254740992", {
          completion_checkpoint: pointer(secondCheckpoint),
          verification_results: [verification({
            completion_checkpoint_id: secondCheckpoint
          })]
        })
      ],
      structured_completion_total: 2
    }),
    page({
      items: [
        page().items[0],
        episode("9007199254740992", {
          completion_checkpoint: pointer(secondCheckpoint),
          artifact_references: [artifact({
            completion_checkpoint_id: secondCheckpoint
          })]
        })
      ],
      structured_completion_total: 2
    }),
    page({ items: [episode("9"), episode("10")], as_of_completion_event_id: "10" }),
    page({ items: [episode("10", { verification_results: [verification({ position: 1 })] })], as_of_completion_event_id: "10", total: 1 }),
    page({
      items: [episode("10", {
        verification_results: [verification(), verification({ position: 1 })]
      })],
      as_of_completion_event_id: "10",
      total: 1
    }),
    page({ items: [episode("10", { artifact_references: [artifact({ work_item_id: "f1cf3691-7d28-4716-94a9-4867b341a685" })] })], as_of_completion_event_id: "10", total: 1 }),
    page({ items: [episode("10", { verification_results: [verification({ created_at: "2026-09-03T18:04:13Z" })] })], as_of_completion_event_id: "10", total: 1 })
  ];
  for (const value of cases) {
    assert.throws(() => decodeCompletionEvidencePage(value, work), /invalid|completion evidence/i);
  }
});

test("history continuation rejects cross-page identity reuse and impossible totals", () => {
  const first = decodeCompletionEvidencePage(page({
    items: [page().items[0]],
    total: 2,
    limit: 1,
    next_cursor: "AAAA"
  }), work);
  const secondCheckpoint = "f1cf3691-7d28-4716-94a9-4867b341a685";
  const validTail = decodeCompletionEvidencePage(page({
    items: [episode("9007199254740992", {
      completion_checkpoint: pointer(secondCheckpoint)
    })],
    total: 2,
    limit: 1,
    next_cursor: null
  }), work, false);
  assert.equal(mergeCompletionEvidencePage(first, first.items, validTail).length, 2);

  const newerCheckpoint = "22222222-2222-4222-8222-222222222222";
  const liveDriftTail = decodeCompletionEvidencePage(page({
    work_version: first.work_version + 2,
    current_completion_checkpoint_id: newerCheckpoint,
    items: [episode("9007199254740992", {
      completion_checkpoint: pointer(secondCheckpoint)
    })],
    total: 2,
    limit: 1,
    next_cursor: null
  }), work, false);
  assert.throws(
    () => mergeCompletionEvidencePage(first, first.items, liveDriftTail),
    /changed while older history was loading/
  );

  for (const tail of [
    { completion_checkpoint: pointer(checkpoint) },
    {
      completion_checkpoint: pointer(secondCheckpoint),
      verification_results: [verification({ completion_checkpoint_id: secondCheckpoint })]
    },
    {
      completion_checkpoint: pointer(secondCheckpoint),
      artifact_references: [artifact({ completion_checkpoint_id: secondCheckpoint })]
    }
  ]) {
    const next = decodeCompletionEvidencePage(page({
      items: [episode("9007199254740992", tail)],
      total: 2,
      structured_completion_total: tail.verification_results || tail.artifact_references ? 2 : 1,
      limit: 1,
      next_cursor: null
    }), work, false);
    assert.throws(() => mergeCompletionEvidencePage(first, first.items, next));
  }

  assert.throws(() => mergeCompletionEvidencePage(
    { ...first, structured_completion_total: 2 },
    first.items,
    { ...validTail, structured_completion_total: 2 }
  ));
});

function stubResponse({ encoding, length, chunks = [], status = 200 }) {
  let getReaderCalls = 0;
  let cancelCalls = 0;
  let readCalls = 0;
  let index = 0;
  const headers = new Headers({ "Content-Type": "application/json" });
  if (encoding !== undefined) headers.set("Content-Encoding", encoding);
  if (length !== undefined) headers.set("Content-Length", length);
  const reader = {
    async read() {
      readCalls += 1;
      return index < chunks.length
        ? { value: chunks[index++], done: false }
        : { value: undefined, done: true };
    },
    async cancel() { cancelCalls += 1; },
    releaseLock() {}
  };
  return {
    response: {
      status,
      headers,
      body: {
        getReader() {
          getReaderCalls += 1;
          return reader;
        },
        async cancel() { cancelCalls += 1; }
      }
    },
    calls: () => ({ getReaderCalls, cancelCalls, readCalls })
  };
}

test("non-identity evidence responses are cancelled before a body reader is acquired", async () => {
  for (const encoding of ["", "gzip", "br", "deflate", "identity,gzip"]) {
    const value = stubResponse({
      encoding,
      status: encoding === "gzip" ? 422 : 200,
      chunks: [Buffer.from('{"detail":"never read"}')]
    });
    await assert.rejects(
      readIdentityEvidenceBytes(value.response),
      IdentityEvidenceResponseError,
      encoding
    );
    assert.deepEqual(value.calls(), {
      getReaderCalls: 0,
      cancelCalls: 1,
      readCalls: 0
    }, encoding);
  }
  assert.equal(identityContentEncoding(new Headers()), true);
  assert.equal(identityContentEncoding(new Headers({ "Content-Encoding": "IdEnTiTy" })), true);
});

test("identity reader is checked-before-copy, bounded, cancellation-safe, and distrusts length", async () => {
  const exact = stubResponse({
    encoding: "IDENTITY",
    length: "8",
    chunks: [Buffer.from("1234"), Buffer.from("5678")]
  });
  assert.equal(Buffer.from(await readIdentityEvidenceBytes(exact.response, 8)).toString(), "12345678");
  assert.equal(exact.calls().readCalls, 3);

  const declaredOver = stubResponse({ length: "9", chunks: [Buffer.from("12345678")] });
  await assert.rejects(readIdentityEvidenceBytes(declaredOver.response, 8));
  assert.deepEqual(declaredOver.calls(), { getReaderCalls: 0, cancelCalls: 1, readCalls: 0 });

  for (const length of [undefined, "-1", "garbage", "7"]) {
    const crossing = stubResponse({
      length,
      chunks: [Buffer.from("1234"), Buffer.from("56789")]
    });
    await assert.rejects(readIdentityEvidenceBytes(crossing.response, 8));
    assert.equal(crossing.calls().getReaderCalls, 1);
    assert.equal(crossing.calls().readCalls, 2);
    assert.equal(crossing.calls().cancelCalls, 1);
  }
});

test("identity reader and proxy retain one bounded evidence buffer without a final clone", async () => {
  const library = await readFile(
    new URL("../lib/completion-evidence.ts", import.meta.url),
    "utf8"
  );
  const proxy = await readFile(
    new URL("../app/api/mnemonic/[...path]/route.ts", import.meta.url),
    "utf8"
  );
  assert.match(library, /new Uint8Array\(maximumBytes\)/);
  assert.match(library, /return bytes\.subarray\(0, size\)/);
  assert.doesNotMatch(library, /chunks\.push\(value\)/);
  assert.match(proxy, /responseBody = evidenceBytes/);
  assert.doesNotMatch(proxy, /Uint8Array\.from\(evidenceBytes\)/);
});

test("identity reader enforces the actual inclusive 3 MiB response boundary", async () => {
  const exact = stubResponse({
    length: String(COMPLETION_EVIDENCE_RESPONSE_MAX_BYTES),
    chunks: [new Uint8Array(COMPLETION_EVIDENCE_RESPONSE_MAX_BYTES)]
  });
  assert.equal(
    (await readIdentityEvidenceBytes(exact.response)).byteLength,
    COMPLETION_EVIDENCE_RESPONSE_MAX_BYTES
  );

  const over = stubResponse({
    chunks: [new Uint8Array(COMPLETION_EVIDENCE_RESPONSE_MAX_BYTES + 1)]
  });
  await assert.rejects(readIdentityEvidenceBytes(over.response));
  assert.deepEqual(over.calls(), { getReaderCalls: 1, cancelCalls: 1, readCalls: 1 });
});

test("identity JSON decoding is fatal for invalid UTF-8 and never accepts truncation", () => {
  assert.deepEqual(decodeIdentityEvidenceJson(Buffer.from('{"ok":true}')), { ok: true });
  assert.throws(() => decodeIdentityEvidenceJson(Uint8Array.from([0x7b, 0xff, 0x7d])));
  assert.throws(() => decodeIdentityEvidenceJson(Buffer.from('{"ok":')));
});

test("rendering and serving policy contains no active content or automatic artifact request path", async () => {
  const component = await readFile(
    new URL("../components/completion-evidence-panel.tsx", import.meta.url),
    "utf8"
  );
  const config = await readFile(new URL("../next.config.ts", import.meta.url), "utf8");
  const nginxPolicy = await readFile(
    new URL("../../deploy/nginx/snippets/mnemonic-dashboard-api-policy.conf", import.meta.url),
    "utf8"
  );
  assert.doesNotMatch(component, /dangerouslySetInnerHTML|iframe|img\s|fetch\s*\(/);
  assert.match(component, /artifactNavigationHref\(artifact\)/);
  assert.match(component, /target="_blank"/);
  assert.match(component, /rel="noopener noreferrer"/);
  assert.match(component, /<bdi/);
  assert.match(component, /id=\{fieldErrorId\(family, rowKey, field\)\}/);
  assert.match(component, /"aria-invalid": true/);
  assert.match(component, /"aria-describedby": fieldErrorId/);
  assert.match(config, /X-DNS-Prefetch-Control/);
  assert.match(config, /value: "off"/);
  assert.match(config, /compress: false/);
  assert.match(nginxPolicy, /gzip off;/);
  assert.match(nginxPolicy, /proxy_set_header Accept-Encoding "identity";/);
  assert.doesNotMatch(nginxPolicy, /proxy_hide_header Content-Encoding/);
  assert.doesNotMatch(nginxPolicy, /^\s*brotli\s+off\s*;/m);
  assert.match(nginxPolicy, /google\/ngx_brotli/);
  assert.match(
    nginxPolicy,
    /add_header Cache-Control "no-store, max-age=0, no-transform" always;/
  );
  assert.match(nginxPolicy, /add_header X-DNS-Prefetch-Control "off" always;/);
});
