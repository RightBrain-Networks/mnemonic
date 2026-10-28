import { ranking, hitRanking, unifiedRanking, semanticDisposition, evidence } from "./search-ranking-fixtures.mjs";
import { disclosure } from "./search-disclosure-fixtures.mjs";
import assert from "node:assert/strict";
import test from "node:test";
import { decodeTranscriptProxyRejection, decodeTranscript, decodeTranscriptPage, decodeTranscriptSettings, decodeTranscriptText, transcriptContentPath, transcriptLibraryPath, transcriptRequest, transcriptStatusLabel, transcriptClientLabel, TRANSCRIPT_JSON_MAX_BYTES, TRANSCRIPT_MAX_BYTES } from "../lib/transcripts.ts";

const project = "7a5dc555-0a6d-4f92-9678-1647524827c8";
const id = "e36a7e53-938f-4c8a-b75a-af9c7331711a";
const operation = "91b9168a-37d1-4a6a-aa1f-bb538b65cb55";
const digest = "a".repeat(64);
const root = `projects/${project}/transcripts`;
const row = { id, project_id: project, work_item_id: operation, lease_generation_id: operation, client: "claude-code", session_id: "session-1", source_path: "/shared/session.jsonl", filename: "session.jsonl", kind: "primary", status: "ready", indexing_started_at: "2026-09-10T12:00:00Z", indexing_completed_at: "2026-09-10T12:00:01Z", error_code: null, size_bytes: 128, mime_type: "application/x-ndjson", format: "claude-code-jsonl", sha256: digest, text_sha256: digest, metadata: { title: ["<script>untrusted</script>"] }, truncated: false, created_at: "2026-09-10T12:00:00Z", snippet: null, score: null };
Object.assign(row, { normalization_status: "ready", normalization_error_code: null,
  normalized_revision: "b".repeat(64), normalized_sha256: "c".repeat(64), normalization_schema_version: 1,
  normalizer_version: 1, normalized_size_bytes: 400, segment_count: 2, normalization_incomplete: false,
  segment_id: null, content_kind: null, snippet_omission_reason: null, matched_fields: [], rank: 1, score_type: "none" });
Object.assign(row, { copy_status: "ready", copy_error_code: null, copied_at: "2026-09-10T12:00:00Z", index_status: "ready", index_error_code: null });
const listing = { ...ranking("", "transcripts"), unsegmented_content_omitted: 0, next_offset: null, page_truncated: false, detail: "full", ...disclosure(project, ["transcripts"]), term_diagnostics: [], items: [row], total: 1, limit: 50, offset: 0, indexing_incomplete: false };
const environment = { MNEMONIC_API_KEY: "k".repeat(64), MNEMONIC_API_URL: "http://api:8000" };
const request = (path, method = "GET", value, headers = {}) => new Request(`http://localhost:3000/api/transcripts/${path}`, { method, headers: { host: "localhost:3000", ...(method !== "GET" ? { origin: "http://localhost:3000", "content-type": "application/json" } : {}), ...headers }, ...(value === undefined ? {} : { body: typeof value === "string" ? value : JSON.stringify(value) }) });

test("transcript metadata and listings enforce project, work, pagination and content scope", () => {
  assert.equal(decodeTranscript(row, project).metadata.title[0], "<script>untrusted</script>");
  assert.equal(decodeTranscriptPage(listing, project).total, 1);
  for (const change of [{ project_id: id }, { work_item_id: "bad" }, { lease_generation_id: "bad" }, { status: "done" }, { size_bytes: -1 }, { indexing_completed_at: "tomorrow" }, { text_sha256: "bad" }, { metadata: { script: "bad" } }]) assert.throws(() => decodeTranscript({ ...row, ...change }, project));
  for (const change of [{ items: [row, row], total: 2 }, { total: 2 }, { offset: 50 }, { items: [{ ...row, snippet: "content" }] }]) assert.throws(() => decodeTranscriptPage({ ...listing, ...change }, project));
  assert.throws(() => decodeTranscriptPage(listing, project, 0, false, id));
  assert.equal(decodeTranscriptPage({ ...listing, ...disclosure(project, ["transcripts"], { fulltext: true }), items: [{ ...row, snippet: "<script>plain</script>" }] }, project, 0, true).items[0].snippet, "<script>plain</script>");
  assert.equal(transcriptLibraryPath(project, operation), `/transcripts?project=${project}&work=${operation}`);
  assert.match(transcriptContentPath(row), new RegExp(`expected_sha256=${digest}$`));
});

test("preview validates unicode pagination and rejects stale indexed text", () => {
  const text = { normalized_revision: null, segments: null, segment_window: null, next_segment_id: null, next_segment_offset: null, next_segment_after: null, transcript_id: id, project_id: project, next_offset: null, text: "Hi 😀", total_chars: 4, offset: 0, limit: 20000, status: "ready", truncated: false, text_sha256: digest };
  assert.equal(decodeTranscriptText(text, id, digest, 0, project).text, "Hi 😀");
  for (const change of [{ text_sha256: "b".repeat(64) }, { total_chars: 5 }, { offset: 1 }, { status: "pending" }, { transcript_id: project }]) assert.throws(() => decodeTranscriptText({ ...text, ...change }, id, digest, 0, project));
});

test("settings enforce revision and distinct configured folder paths", () => {
  const settings = { enabled: true, revision: 1, directories: ["/shared/transcripts/project"], allowed_roots: ["/shared/transcripts"] };
  assert.equal(decodeTranscriptSettings(settings).revision, 1);
  for (const change of [{ revision: 0 }, { enabled: "true" }, { directories: ["/shared", "/shared"] }, { directories: ["relative"] }, { allowed_roots: [null] }]) assert.throws(() => decodeTranscriptSettings({ ...settings, ...change }));
});

test("imported transcripts require null lease provenance and remain scoped", () => {
  const imported = { ...row, kind: "imported", work_item_id: null, lease_generation_id: null, session_id: null };
  assert.equal(decodeTranscript(imported, project).kind, "imported");
  assert.equal(decodeTranscriptPage({ ...listing, items: [imported] }, project).total, 1);
  for (const change of [{ work_item_id: operation }, { lease_generation_id: operation }, { session_id: "invented" }, { kind: "primary" }]) {
    assert.throws(() => decodeTranscript({ ...imported, ...change }, project));
  }
  assert.throws(() => decodeTranscriptPage({ ...listing, items: [imported] }, project, 0, false, operation));
});

test("import confirmation binds counts, project, folder and operation", async () => {
  const { decodeTranscriptImport, decodeTranscriptImportRejection } = await import("../lib/transcripts.ts");
  const result = { project_id: project, client_operation_id: operation, directory: "/shared", imported: 1, existing: 2, skipped: 0 };
  assert.equal(decodeTranscriptImport(result, project, operation, "/shared").imported, 1);
  for (const change of [{ project_id: id }, { client_operation_id: id }, { directory: "/other" }, { imported: -1 }, { imported: 5000, existing: 1 }, { skipped: 50001 }, { extra: true }]) {
    assert.throws(() => decodeTranscriptImport({ ...result, ...change }, project, operation, "/shared"));
  }
  const rejection = { detail: { code: "transcript_import_scan_failed", message: "Cannot read this folder.", context: {} } };
  assert.equal(decodeTranscriptImportRejection(422, rejection), rejection.detail.message);
  assert.equal(decodeTranscriptImportRejection(500, rejection), null);
  assert.equal(decodeTranscriptImportRejection(409, { detail: { code: "transcript_import_conflict", message: "Conflict" } }), null);
  assert.equal(decodeTranscriptImportRejection(409, { detail: { code: "transcript_disabled", message: "Enable transcripts first." } }), "Enable transcripts first.");
});

test("Codex transcripts retain client metadata and display their provider name", () => {
  const codex = { ...row, client: "codex", format: "codex-jsonl" };
  assert.equal(decodeTranscript(codex, project).client, "codex");
  assert.equal(transcriptClientLabel(codex.client), "OpenAI Codex");
  assert.equal(transcriptClientLabel("claude_code"), "Claude Code");
  assert.equal(transcriptClientLabel("future-client"), "future-client");
});

test("normalization coverage and matched content kinds remain explicit in transcript results", () => {
  const contentKinds = ["human_text", "assistant_text"];
  const matched = { ...row, snippet: "needle", matched_fields: ["content"], segment_id: "a".repeat(24), content_kind: "human_text" };
  const result = { ...listing, ...disclosure(project, ["transcripts"], { fulltext: true,
    filters: { transcripts: { content_kinds: contentKinds } } }), items: [matched] };
  assert.equal(decodeTranscriptPage(result, project, 0, true, undefined, contentKinds).items[0].segment_id, matched.segment_id);
  for (const change of [{ content_kind: "tool_result" }, { normalized_revision: null }, { segment_id: null },
    { normalization_incomplete: true }, { normalization_status: "failed" }, { normalized_size_bytes: -1 },
    { normalization_schema_version: 0 }, { segment_count: true }]) {
    assert.throws(() => decodeTranscriptPage({ ...result, items: [{ ...matched, ...change }] }, project, 0, true, undefined, contentKinds));
  }
  assert.throws(() => decodeTranscriptPage(result, project, 0, true, undefined, ["tool_result"]));
  assert.equal(decodeTranscriptPage({ ...result, indexing_incomplete: true,
    items: [{ ...matched, normalization_status: "failed", normalization_incomplete: true }] }, project, 0, true, undefined, contentKinds).indexing_incomplete, true);
});

test("transcript status distinguishes search limits from normalization and metadata warnings", () => {
  assert.equal(transcriptStatusLabel(row), "Indexed");
  assert.equal(transcriptStatusLabel({ ...row, normalization_incomplete: true }), "Indexed · Coverage incomplete");
  assert.equal(transcriptStatusLabel({ ...row, truncated: true }), "Indexed · Search text limited");
  assert.equal(transcriptStatusLabel({ ...row, truncated: true, normalization_incomplete: true }), "Indexed · Search text limited · Coverage incomplete");
  assert.equal(transcriptStatusLabel({ ...row, metadata: { "transcript:metadata_limited": ["true"] } }), "Indexed · Metadata limited");
});

test("native session metadata validates independently of reporting provenance", () => {
  const metadata = { ...row, last_updated_at: "2026-02-01T14:30:00Z", index_created_at: row.indexing_completed_at, session_ids: ["native-child"], models: ["fixture-model"] };
  assert.equal(decodeTranscript(metadata, project).session_ids[0], "native-child");
  assert.equal(decodeTranscript(metadata, project).session_id, "session-1");
  for (const change of [{last_updated_at: "yesterday"}, {index_created_at: "tomorrow"}, {models: Array(9).fill("model")}, {session_ids: [null]}]) assert.throws(() => decodeTranscript({...metadata,...change}, project));
});

test("transcript page budgets accommodate valid long Unicode paths and escaped JSON", async (context) => {
  const filename = "😀".repeat(4095);
  const page = { ...listing, total: 50, items: Array.from({ length: 50 }, (_, index) => ({
    ...row, id: `${id.slice(0, -12)}${index.toString(16).padStart(12, "0")}`,
    filename, source_path: `/${filename}`
  })) };
  const encoded = JSON.stringify(page).replace(/[^\x00-\x7f]/g, (character) => `\\u${character.charCodeAt(0).toString(16).padStart(4, "0")}`);
  assert.ok(Buffer.byteLength(encoded) > 4 * 1024 * 1024);
  assert.ok(Buffer.byteLength(encoded) < TRANSCRIPT_JSON_MAX_BYTES);
  const upstream = () => new Response(encoded, { headers: { "content-type": "application/json" } });
  context.mock.method(globalThis, "fetch", upstream);
  const browserValue = await transcriptRequest(`/api/transcripts/${root}`);
  assert.equal(decodeTranscriptPage(browserValue, project).items[0].filename, filename);
});

test("only exact gateway rejection envelopes establish an undispatched transcript write", () => {
  const value = { detail: { code: "transcript_proxy_rejected", message: "Invalid transcript settings request." } };
  assert.equal(decodeTranscriptProxyRejection(400, value), value.detail.message);
  for (const [status, body] of [
    [500, value], [503, value], [409, value],
    [400, { detail: "Invalid transcript settings request." }],
    [400, { detail: { code: "transcript_proxy_rejected", message: "Unknown rejection." } }],
    [400, { ...value, extra: true }], [400, { detail: { ...value.detail, extra: true } }]
  ]) assert.equal(decodeTranscriptProxyRejection(status, body), null);
});
